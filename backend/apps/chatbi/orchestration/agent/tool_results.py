"""ChatBI 对 Tool 结果的集中解释入口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from apps.chatbi.models import ResultArtifactWriteData
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.services.execution import ResultArtifactWriteError
from apps.conversation import ChatRecordExecutionType
from apps.tool import (
    RetryAdvice,
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)


class ToolControlAction(StrEnum):
    NONE = "none"
    CLARIFY = "clarify"
    FINISH = "finish"


@dataclass(frozen=True, slots=True)
class SuggestedDomainEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResultProjection:
    result: ToolResult[Any]
    state_patch: dict[str, Any] = field(default_factory=dict)
    control: ToolControlAction = ToolControlAction.NONE
    control_data: dict[str, Any] = field(default_factory=dict)
    events: tuple[SuggestedDomainEvent, ...] = ()
    audit_summary: dict[str, Any] = field(default_factory=dict)


class ChatBIToolResultProcessor:
    """生成状态补丁、控制动作和事件建议，不直接修改共享状态。"""

    def process(
        self,
        context: AgentToolContext,
        tool_name: str,
        result: ToolResult[Any],
    ) -> ToolResultProjection:
        if result.status != ToolStatus.SUCCEEDED or result.data is None:
            operation_state = {
                key: result.metadata[key]
                for key in (
                    "underlying_operation_started",
                    "underlying_operation_completed",
                    "underlying_operation_may_have_completed",
                )
                if key in result.metadata
            }
            return ToolResultProjection(
                result=result,
                audit_summary={
                    "success": False,
                    "status": result.status.value,
                    "error_code": result.error_code,
                    **operation_state,
                },
            )

        payload = result.data.model_dump(mode="json")
        base_summary = {"success": True, "status": result.status.value}
        if tool_name == "search_semantic_assets":
            return self._semantic_assets(context, result, payload, base_summary)
        if tool_name == "get_dataset_schema":
            tables = self._merge_tables(
                context,
                [
                    str(item.get("table"))
                    for item in payload.get("tables") or []
                    if isinstance(item, dict) and item.get("table")
                ],
            )
            return ToolResultProjection(
                result=result,
                state_patch={"allowed_tables": tables},
                audit_summary={
                    **base_summary,
                    "table_count": payload.get("table_count"),
                },
            )
        if tool_name == "compile_semantic_sql":
            return ToolResultProjection(
                result=result,
                state_patch={
                    "compiled_sql": payload.get("sql"),
                    "allowed_tables": self._merge_tables(
                        context,
                        payload.get("tables") or [],
                    ),
                },
                events=(
                    SuggestedDomainEvent(
                        "sql-generated",
                        {"sql": payload.get("sql")},
                    ),
                ),
                audit_summary={**base_summary, "sql": payload.get("sql")},
            )
        if tool_name == "validate_sql":
            return ToolResultProjection(
                result=result,
                events=(
                    SuggestedDomainEvent(
                        "sql-validated",
                        {"sql": payload.get("sql")},
                    ),
                ),
                audit_summary={**base_summary, "sql": payload.get("sql")},
            )
        if tool_name == "execute_sql":
            return self._execution(context, result, payload, base_summary)
        if tool_name == "clarify":
            return ToolResultProjection(
                result=result,
                control=ToolControlAction.CLARIFY,
                control_data=payload,
                audit_summary=base_summary,
            )
        if tool_name == "finish":
            return ToolResultProjection(
                result=result,
                control=ToolControlAction.FINISH,
                control_data=payload,
                audit_summary=base_summary,
            )
        if tool_name in {"search_terminology", "get_sql_examples"}:
            return ToolResultProjection(
                result=result,
                audit_summary={**base_summary, "count": payload.get("count")},
            )
        return ToolResultProjection(result=result, audit_summary=base_summary)

    def _semantic_assets(
        self,
        context: AgentToolContext,
        result: ToolResult[Any],
        payload: dict[str, Any],
        base_summary: dict[str, Any],
    ) -> ToolResultProjection:
        package = payload.get("package") or {}
        scope = payload.get("scope") or {}
        asset_ids = sorted(
            {
                int(item["asset_id"])
                for item in scope.get("allowed_assets") or []
                if isinstance(item, dict) and isinstance(item.get("asset_id"), int)
            }
        )
        return ToolResultProjection(
            result=result,
            state_patch={
                "semantic_package": package,
                "semantic_scope": scope,
                # 保留审计和恢复所需的派生字段，唯一来源是检索决策白名单。
                "semantic_asset_ids": asset_ids,
                "allowed_tables": self._merge_tables(
                    context,
                    package.get("tables") or [],
                ),
            },
            audit_summary={
                **base_summary,
                "semantic_status": package.get("status"),
                "metrics": package.get("metrics"),
                "dimensions": package.get("dimensions"),
                "tables": package.get("tables"),
            },
        )

    def _execution(
        self,
        context: AgentToolContext,
        result: ToolResult[Any],
        payload: dict[str, Any],
        base_summary: dict[str, Any],
    ) -> ToolResultProjection:
        if (
            context.result_artifact_service is None
            or not context.execution_id
            or context.chat_id is None
            or context.record_id is None
        ):
            return self._artifact_failure("result_artifact_service_required")
        full_data = result.metadata.get("full_data")
        rows = full_data if isinstance(full_data, list) else []
        try:
            artifact_ref = context.result_artifact_service.save(
                ResultArtifactWriteData(
                    execution_id=context.execution_id,
                    execution_type=ChatRecordExecutionType.AGENT,
                    chat_id=context.chat_id,
                    record_id=context.record_id,
                    kind="sql_result",
                    payload={
                        "query_id": "query-0",
                        "fields": payload.get("fields") or [],
                        "rows": rows,
                        "row_count": payload.get("row_count") or 0,
                    },
                    metadata={
                        "query_id": "query-0",
                        "row_count": payload.get("row_count") or 0,
                    },
                )
            )
        except ResultArtifactWriteError:
            return self._artifact_failure("sql_result_artifact_write_failed")

        compiled = context.state.get("compiled_sql")
        sql = str(payload.get("sql") or "")
        sql_source = (
            "compiled"
            if isinstance(compiled, str)
            and self._normalize_sql(sql) == self._normalize_sql(compiled)
            else "manual"
        )
        artifact_payload = artifact_ref.model_dump(mode="json")
        execution = {
            "sql": sql,
            "fields": payload.get("fields") or [],
            "row_count": payload.get("row_count") or 0,
            "sample_rows": payload.get("sample_rows") or [],
            "artifact_ref": artifact_payload,
            "sql_source": sql_source,
        }
        projected_result = result.with_updates(
            metadata={
                **result.metadata,
                "artifact_ref": artifact_payload,
                "sql_source": sql_source,
            }
        )
        return ToolResultProjection(
            result=projected_result,
            state_patch={"last_execution": execution, "full_data": rows},
            events=(
                SuggestedDomainEvent(
                    "sql-executed",
                    {
                        "row_count": execution["row_count"],
                        "fields": execution["fields"],
                    },
                ),
            ),
            audit_summary={
                **base_summary,
                "row_count": execution["row_count"],
                "fields": execution["fields"],
            },
        )

    @staticmethod
    def _artifact_failure(error_code: str) -> ToolResultProjection:
        result = ToolResult.failed(
            "SQL 结果 Artifact 写入失败。",
            error_code=error_code,
            error_category=ToolErrorCategory.DOMAIN,
            retry_advice=RetryAdvice.NEVER,
        )
        return ToolResultProjection(
            result=result,
            audit_summary={
                "success": False,
                "status": result.status.value,
                "error_code": result.error_code,
            },
        )

    @staticmethod
    def _merge_tables(
        context: AgentToolContext,
        new_tables: list[Any],
    ) -> list[str]:
        tables = {
            str(table)
            for table in context.state.get("allowed_tables") or []
            if str(table).strip()
        }
        tables.update(str(table) for table in new_tables if str(table).strip())
        return sorted(tables)

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return " ".join(sql.lower().split()).rstrip(";")


__all__ = [
    "ChatBIToolResultProcessor",
    "SuggestedDomainEvent",
    "ToolControlAction",
    "ToolResultProjection",
]
