"""ASSISTED 模式的受控 NL2SQL 兜底。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from apps.chatbi.errors import QuestionModelError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.services.understanding.model_invocation import StructuredModelService
from apps.datasource import (
    DatasourceQueryRequest,
    DatasourceQueryService,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.semantic import DatasetSchema


@dataclass(frozen=True, slots=True)
class FallbackSQLInput:
    """物理 Schema 兜底所需的最小可信输入。"""

    question: str
    datasource_id: int
    user_id: int
    workspace_id: int
    schema: DatasetSchema | dict[str, Any]
    exemplars: list[dict[str, Any]] = field(default_factory=list)
    selected_tables: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FallbackSQLResult:
    """兜底 SQL 的执行结果，始终标记为非认证口径。"""

    sql: str
    tables: list[str]
    fields: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    sample_rows: list[dict[str, Any]]
    stats_summary: dict[str, Any]
    certified: bool = False
    sql_source: str = "assisted_fallback"
    warnings: list[str] = field(default_factory=list)


class _FallbackModelOutput(BaseModel):
    sql: str = Field(min_length=1)
    tables: list[str] = Field(default_factory=list)


class AssistedFallbackSQLService:
    """生成 SQL 后复用 DatasourceQueryService 的校验和执行三道闸。"""

    def __init__(
        self,
        model_service: StructuredModelService,
        query_service: DatasourceQueryService,
    ) -> None:
        if model_service is None:
            raise ValueError("ASSISTED_FALLBACK_MODEL_SERVICE_REQUIRED")
        if query_service is None:
            raise ValueError("ASSISTED_FALLBACK_QUERY_SERVICE_REQUIRED")
        self._model_service = model_service
        self._query_service = query_service

    def generate_and_execute(self, data: FallbackSQLInput) -> FallbackSQLResult:
        self._validate_input(data)
        output = self._generate_sql(data)
        request = DatasourceQueryRequest(
            sql=output.sql,
            datasource_id=data.datasource_id,
            subject=DatasourceQuerySubject(
                user_id=data.user_id,
                workspace_id=data.workspace_id,
            ),
            selected_tables=list(data.selected_tables or output.tables),
        )
        validated = self._query_service.validate(request)
        if validated.status is not DatasourceQueryStatus.SUCCEEDED or validated.data is None:
            raise ValueError(
                f"ASSISTED_FALLBACK_SQL_REJECTED:{validated.error_code or 'query_validation_failed'}"
            )
        executed = self._query_service.execute(request.model_copy(update={"sql": validated.data.sql}))
        if executed.status is not DatasourceQueryStatus.SUCCEEDED or executed.data is None:
            raise ValueError(
                f"ASSISTED_FALLBACK_SQL_FAILED:{executed.error_code or 'query_execution_failed'}"
            )
        payload = executed.data
        return FallbackSQLResult(
            sql=payload.sql,
            tables=list(payload.effective_tables or payload.tables),
            fields=list(payload.fields),
            rows=list(payload.full_data),
            row_count=payload.row_count,
            sample_rows=list(payload.sample_rows),
            stats_summary=dict(payload.stats_summary),
            warnings=["本次回答来自 ASSISTED 兜底 SQL，非认证语义口径。"],
        )

    def _generate_sql(self, data: FallbackSQLInput) -> _FallbackModelOutput:
        schema_payload = (
            data.schema.model_dump(mode="json")
            if isinstance(data.schema, DatasetSchema)
            else dict(data.schema)
        )
        prompt = (
            "请基于物理 Schema 和已审核示例生成只读 SQL。只能使用 Schema 中的表和字段，"
            "不得生成 INSERT、UPDATE、DELETE、DDL、子查询或外部表引用。"
            "只返回 JSON：{\"sql\":\"...\",\"tables\":[\"...\"]}。\n"
            + json.dumps(
                {
                    "question": data.question,
                    "schema": schema_payload,
                    "exemplars": data.exemplars[:5],
                    "selected_tables": data.selected_tables,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        try:
            payload = self._model_service.invoke(
                QuestionModelInvocationData(
                    stage="assisted_fallback_sql",
                    system_prompt="你是受控的 ASSISTED SQL 生成器。",
                    user_prompt=prompt,
                    json_mode=QuestionModelJSONMode.STRICT,
                )
            ).payload
            return _FallbackModelOutput.model_validate(payload)
        except (QuestionModelError, ValidationError) as exc:
            raise ValueError("ASSISTED_FALLBACK_SQL_OUTPUT_INVALID") from exc

    @staticmethod
    def _validate_input(data: FallbackSQLInput) -> None:
        if not data.question.strip():
            raise ValueError("ASSISTED_FALLBACK_QUESTION_REQUIRED")
        if data.datasource_id <= 0 or data.user_id <= 0 or data.workspace_id <= 0:
            raise ValueError("ASSISTED_FALLBACK_SCOPE_REQUIRED")


__all__ = [
    "AssistedFallbackSQLService",
    "FallbackSQLInput",
    "FallbackSQLResult",
]
