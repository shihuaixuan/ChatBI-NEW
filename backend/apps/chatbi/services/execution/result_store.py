"""基于 ResultArtifactService 的命名结果集存储。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from apps.chatbi.models.dto.analysis_plan import (
    ResultSetKind,
    ResultSetRef,
    ResultSetSnapshot,
    ResultSetSummary,
    build_result_set_id,
)
from apps.chatbi.models.dto.result_artifact import (
    ChatBIResultArtifactRef,
    ResultArtifactReadInput,
    ResultArtifactSnapshot,
    ResultArtifactWriteData,
)
from apps.chatbi.services.execution.result_artifacts import ResultArtifactService
from apps.conversation import ChatRecordExecutionType


class ResultArtifactStore(Protocol):
    """ResultStore 对 Artifact 层的最小依赖。"""

    def save(self, data: ResultArtifactWriteData) -> ChatBIResultArtifactRef: ...

    def read(self, data: ResultArtifactReadInput) -> ResultArtifactSnapshot: ...


class ResultStore:
    """注册、读取并摘要化一个 Run 内的命名结果集。"""

    ARTIFACT_KIND = ResultArtifactService.NAMED_RESULT_SET_KIND
    LEGACY_QUERY_ID = "query-0"

    def __init__(self, artifact_service: ResultArtifactStore) -> None:
        self._artifact_service = artifact_service

    def register(
        self,
        *,
        execution_id: str,
        execution_type: ChatRecordExecutionType,
        chat_id: int,
        record_id: int,
        plan_id: str,
        node_id: str,
        kind: ResultSetKind,
        fields: list[str],
        rows: list[dict[str, Any]],
        row_count: int,
        source_sql: str | None = None,
        semantic_refs: list[dict[str, Any]] | None = None,
    ) -> ResultSetRef:
        result_set_id = build_result_set_id(plan_id, node_id)
        normalized_fields = [str(field) for field in fields]
        json_rows = [_json_row(row) for row in rows]
        normalized_semantic_refs = list(semantic_refs or [])
        created_at = datetime.now()
        # 命名结果集与旧结果统一经过同一个 result_artifact_service.save 网关。
        result_artifact_service = self._artifact_service
        artifact_ref = result_artifact_service.save(
            ResultArtifactWriteData(
                execution_id=execution_id,
                execution_type=execution_type,
                chat_id=chat_id,
                record_id=record_id,
                kind=self.ARTIFACT_KIND,
                payload={
                    "result_set_id": result_set_id,
                    # 旧消费者仍可通过 query_id 识别单查询结果。
                    "query_id": node_id,
                    "fields": normalized_fields,
                    "rows": json_rows,
                    "row_count": row_count,
                },
                metadata={
                    "result_set_id": result_set_id,
                    "plan_id": plan_id,
                    "node_id": node_id,
                    "query_id": node_id,
                    "result_kind": kind.value,
                    "row_count": row_count,
                    "source_sql": source_sql,
                    "semantic_refs": normalized_semantic_refs,
                    "created_at": created_at.isoformat(),
                    "numeric_stats": _numeric_stats(rows),
                },
            )
        )
        return ResultSetRef(
            result_set_id=result_set_id,
            plan_id=plan_id,
            node_id=node_id,
            kind=kind,
            artifact_ref=artifact_ref,
            fields=tuple(normalized_fields),
            row_count=row_count,
            source_sql=source_sql,
            semantic_refs=tuple(normalized_semantic_refs),
            created_at=created_at,
        )

    def read(
        self,
        ref: ResultSetRef,
        *,
        execution_id: str,
        execution_type: ChatRecordExecutionType,
        chat_id: int,
        record_id: int,
    ) -> ResultSetSnapshot:
        snapshot = self._artifact_service.read(
            ResultArtifactReadInput(
                artifact_id=ref.artifact_ref.artifact_id,
                execution_id=execution_id,
                execution_type=execution_type,
                chat_id=chat_id,
                record_id=record_id,
                kind=self.ARTIFACT_KIND,
                expected_metadata={
                    "result_set_id": ref.result_set_id,
                    "plan_id": ref.plan_id,
                    "node_id": ref.node_id,
                    "result_kind": ref.kind.value,
                    "row_count": ref.row_count,
                },
            )
        )
        payload = snapshot.payload
        if payload.get("result_set_id") != ref.result_set_id:
            raise ValueError("RESULT_SET_PAYLOAD_ID_MISMATCH")
        fields = payload.get("fields")
        rows = payload.get("rows")
        row_count = payload.get("row_count")
        if not isinstance(fields, list) or not isinstance(rows, list) or not isinstance(row_count, int):
            raise ValueError("RESULT_SET_PAYLOAD_INVALID")
        if fields != list(ref.fields) or row_count != ref.row_count:
            raise ValueError("RESULT_SET_PAYLOAD_SUMMARY_MISMATCH")
        normalized_rows = [item for item in rows if isinstance(item, dict)]
        if len(normalized_rows) != len(rows):
            raise ValueError("RESULT_SET_ROWS_INVALID")
        numeric_stats = snapshot.metadata.get("numeric_stats")
        return ResultSetSnapshot(
            ref=ref,
            rows=tuple(normalized_rows),
            numeric_stats=(numeric_stats if isinstance(numeric_stats, dict) else {}),
        )

    def summarize(
        self,
        snapshot: ResultSetSnapshot,
        *,
        sample_size: int = 10,
    ) -> ResultSetSummary:
        if sample_size < 0:
            raise ValueError("RESULT_SET_SAMPLE_SIZE_INVALID")
        return ResultSetSummary(
            result_set_id=snapshot.ref.result_set_id,
            fields=snapshot.ref.fields,
            row_count=snapshot.ref.row_count,
            sample_rows=snapshot.rows[:sample_size],
            numeric_stats=(
                snapshot.numeric_stats
                or _numeric_stats([dict(row) for row in snapshot.rows])
            ),
        )


def _numeric_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, int | float | str]]:
    """统一把数值转 Decimal 计算，避免混合类型和浮点累加误差。"""

    numeric_values: dict[str, list[Decimal]] = {}
    for row in rows:
        for field, value in row.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                continue
            numeric_values.setdefault(str(field), []).append(Decimal(str(value)))
    return {
        field: {
            "count": len(values),
            "min": _json_number(min(values)),
            "max": _json_number(max(values)),
            "sum": _json_number(sum(values, Decimal(0))),
        }
        for field, values in numeric_values.items()
        if values
    }


def _json_number(value: Decimal) -> str:
    """Decimal 统一用字符串进入 JSON，避免静默丢失精度。"""

    return str(value)


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    """把底层 JSON 不支持的数值和时间值转换成稳定文本。"""

    return {str(key): _json_value(value) for key, value in row.items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


__all__ = ["ResultStore"]
