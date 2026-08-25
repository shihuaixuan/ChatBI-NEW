"""ChatBI 对 Tool 结果的集中解释入口。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from apps.chatbi.models import ResultSetKind
from apps.chatbi.orchestration.agent.semantic_projection import (
    refresh_semantic_projection,
)
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.services.execution.result_artifacts import ResultArtifactWriteError
from apps.chatbi.services.execution.result_store import ResultArtifactStore, ResultStore
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
    REFUSE = "refuse"


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
            state_patch: dict[str, Any] = {}
            if (
                tool_name == "execute_sql"
                and result.retry_advice == RetryAdvice.CORRECT_INPUT
            ):
                # SQL 内容需要修正时清除旧产物，让下一轮回到编译或校验，而不是重放同一 SQL。
                if context.state.get("compiled_sql") is not None:
                    state_patch["compiled_sql"] = None
                if context.state.get("validated_sql") is not None:
                    state_patch["validated_sql"] = None
            operation_state = {
                key: result.metadata[key]
                for key in (
                    "underlying_operation_started",
                    "underlying_operation_completed",
                    "underlying_operation_may_have_completed",
                )
                if key in result.metadata
            }
            refusal_answer = _terminal_refusal_answer(result)
            return ToolResultProjection(
                result=result,
                state_patch=state_patch,
                control=(
                    ToolControlAction.REFUSE
                    if refusal_answer is not None
                    else ToolControlAction.NONE
                ),
                control_data=(
                    {"answer": refusal_answer} if refusal_answer is not None else {}
                ),
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
        if tool_name == "parse_time_range":
            return self._time_range(context, result, payload, base_summary)
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
                state_patch={
                    "allowed_tables": tables,
                    "physical_schema_loaded": True,
                },
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
                    "validated_sql": None,
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
                state_patch={"validated_sql": payload.get("sql")},
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
        # 候选检索阶段没有绑定白名单；资产 ID 只能在后续模型绑定并校验后写入。
        asset_ids: list[int] = []
        state_patch = {
            "semantic_package": package,
            "semantic_scope": scope,
            # 保留审计和恢复所需的派生字段，唯一来源是检索决策白名单。
            "semantic_asset_ids": asset_ids,
            "allowed_tables": self._merge_tables(
                context,
                package.get("tables") or [],
            ),
        }
        for key in (
            "semantic_bundle",
            "semantic_payload",
            "semantic_retrieval_request",
            "semantic_retrieval_filters",
            "semantic_schema",
        ):
            if key in result.metadata:
                state_patch[key] = result.metadata[key]
        package, scope, snapshot_patch = refresh_semantic_projection(
            {**context.state, **state_patch},
            package,
            scope,
            schema_data=result.metadata.get("semantic_schema"),
        )
        state_patch.update(snapshot_patch)
        state_patch["semantic_package"] = package
        state_patch["semantic_scope"] = scope
        return ToolResultProjection(
            result=result,
            state_patch=state_patch,
            audit_summary={
                **base_summary,
                "semantic_status": package.get("status"),
                "metrics": package.get("metrics"),
                "dimensions": package.get("dimensions"),
                "tables": package.get("tables"),
                "semantic_enforcement": scope.get("semantic_enforcement"),
                "plan_fingerprint": (
                    (scope.get("query_plan") or {}).get("fingerprint")
                    if isinstance(scope.get("query_plan"), dict)
                    else None
                ),
                "plan_status": (
                    (scope.get("query_plan") or {}).get("validation_status")
                    if isinstance(scope.get("query_plan"), dict)
                    else None
                ),
                "validation_reason_codes": (
                    (scope.get("validation_report") or {}).get("reason_codes")
                    if isinstance(scope.get("validation_report"), dict)
                    else [],
                ),
            },
        )

    def _time_range(
        self,
        context: AgentToolContext,
        result: ToolResult[Any],
        payload: dict[str, Any],
        base_summary: dict[str, Any],
    ) -> ToolResultProjection:
        """把时间工具结果写回问题理解，并刷新已完成检索的查询计划。"""

        status = str(payload.get("status") or "")
        state_patch: dict[str, Any] = {
            "time_parse_status": status,
            "time_parse_raw": payload.get("raw"),
        }
        if status == "resolved":
            understanding = deepcopy(context.state.get("question_understanding"))
            if isinstance(understanding, dict):
                intent = understanding.get("intent")
                if isinstance(intent, dict):
                    time_range = dict(intent.get("time_range") or {})
                    time_range.update(
                        {
                            "raw": payload.get("raw"),
                            "value_status": "provided",
                            "normalized": payload.get("normalized"),
                            "interpretation_source": "jionlp",
                        }
                    )
                    intent["time_range"] = time_range
                    state_patch["question_understanding"] = understanding
            state_patch["time_range"] = payload.get("normalized")
            package = context.state.get("semantic_package")
            scope = context.state.get("semantic_scope")
            if isinstance(package, dict) and isinstance(scope, dict):
                (
                    refreshed_package,
                    refreshed_scope,
                    snapshot_patch,
                ) = refresh_semantic_projection(
                    {**context.state, **state_patch},
                    package,
                    scope,
                    schema_data=context.state.get("semantic_schema"),
                )
                state_patch.update(
                    {
                        "semantic_package": refreshed_package,
                        "semantic_scope": refreshed_scope,
                        **snapshot_patch,
                    }
                )

        return ToolResultProjection(
            result=result,
            state_patch=state_patch,
            audit_summary={
                **base_summary,
                "time_status": status,
                "normalized": payload.get("normalized"),
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
            # ResultStore 内部仍以 result_artifact_service.save 作为统一 Artifact 网关。
            result_store = context.result_store or ResultStore(
                cast(ResultArtifactStore, context.result_artifact_service)
            )
            plan_id = self._result_plan_id(context)
            # 新编排为每个 AnalysisPlan 节点写入独立结果集；旧 ReAct 未设置时继续双写 query-0。
            configured_node_id = context.state.get("result_node_id")
            node_id = (
                str(configured_node_id).strip()
                if isinstance(configured_node_id, str) and configured_node_id.strip()
                else ResultStore.LEGACY_QUERY_ID
            )
            result_set_ref = result_store.register(
                execution_id=context.execution_id,
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=context.chat_id,
                record_id=context.record_id,
                plan_id=plan_id,
                node_id=node_id,
                kind=ResultSetKind.QUERY,
                fields=payload.get("fields") or [],
                rows=rows,
                row_count=payload.get("row_count") or 0,
                source_sql=str(payload.get("sql") or "") or None,
                semantic_refs=self._semantic_refs(context),
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
        result_set_payload = result_set_ref.model_dump(mode="json")
        artifact_payload = result_set_ref.artifact_ref.model_dump(mode="json")
        execution = {
            "sql": sql,
            "fields": payload.get("fields") or [],
            "row_count": payload.get("row_count") or 0,
            "sample_rows": payload.get("sample_rows") or [],
            "artifact_ref": artifact_payload,
            "result_set_id": result_set_ref.result_set_id,
            "sql_source": sql_source,
        }
        projected_result = result.with_updates(
            metadata={
                **result.metadata,
                "artifact_ref": artifact_payload,
                "result_set_ref": result_set_payload,
                "sql_source": sql_source,
            }
        )
        existing_result_sets = context.state.get("result_sets")
        if not isinstance(existing_result_sets, dict):
            existing_result_sets = {}
        return ToolResultProjection(
            result=projected_result,
            state_patch={
                "last_execution": execution,
                "full_data": rows,
                "result_sets": {
                    **existing_result_sets,
                    result_set_ref.result_set_id: result_set_payload,
                },
            },
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
    def _result_plan_id(context: AgentToolContext) -> str:
        analysis_plan = context.state.get("analysis_plan")
        if isinstance(analysis_plan, dict):
            plan_id = analysis_plan.get("id")
            if isinstance(plan_id, str) and plan_id.strip():
                return plan_id.strip()
        if not context.execution_id:
            raise ValueError("RESULT_SET_EXECUTION_ID_REQUIRED")
        return f"legacy-{context.execution_id.replace(':', '-')}"

    @staticmethod
    def _semantic_refs(context: AgentToolContext) -> list[dict[str, Any]]:
        scope = context.state.get("semantic_scope")
        if not isinstance(scope, dict):
            return []
        return [
            dict(item)
            for item in scope.get("allowed_assets") or []
            if isinstance(item, dict)
        ]

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


def _terminal_refusal_answer(result: ToolResult[Any]) -> str | None:
    """把不可重试的语义组合冲突收口为正常拒答。"""

    return semantic_incompatibility_answer(result.error_code)


def semantic_incompatibility_answer(error_code: str | None) -> str | None:
    """把检索期和路由期的指标维度不兼容统一为用户可理解的拒答。"""

    normalized = str(error_code or "").upper()
    if not normalized.startswith("SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE"):
        return None
    return (
        "当前指标不能按所选维度进行查询或贡献度分解，因为已发布的语义契约中"
        "没有这组指标和维度的可执行关系。请改用该指标已支持的维度，或先在"
        "语义模型中发布对应的指标维度能力。"
    )


__all__ = [
    "ChatBIToolResultProcessor",
    "semantic_incompatibility_answer",
    "SuggestedDomainEvent",
    "ToolControlAction",
    "ToolResultProjection",
]
