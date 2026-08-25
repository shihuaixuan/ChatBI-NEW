"""基于 ResultArtifactService 的命名结果集存储。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from threading import RLock
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

    def find_by_idempotency_key(
        self,
        *,
        execution_id: str,
        kind: str,
        idempotency_key: str,
    ) -> ResultArtifactSnapshot | None: ...


class ResultStore:
    """注册、读取并摘要化一个 Run 内的命名结果集。"""

    ARTIFACT_KIND = ResultArtifactService.NAMED_RESULT_SET_KIND
    LEGACY_QUERY_ID = "query-0"

    def __init__(self, artifact_service: ResultArtifactStore) -> None:
        self._artifact_service = artifact_service
        self._write_lock = RLock()
        self._idempotent_refs: dict[tuple[str, str], tuple[str, ResultSetRef]] = {}

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
        attempt: int = 1,
        source_sql: str | None = None,
        semantic_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> ResultSetRef:
        if attempt <= 0:
            raise ValueError("RESULT_SET_ATTEMPT_INVALID")
        if idempotency_key is not None and not idempotency_key.strip():
            raise ValueError("RESULT_SET_IDEMPOTENCY_KEY_INVALID")
        if idempotency_key is not None and len(idempotency_key) > 256:
            raise ValueError("RESULT_SET_IDEMPOTENCY_KEY_TOO_LONG")
        result_set_id = build_result_set_id(plan_id, node_id)
        normalized_fields = [str(field) for field in fields]
        json_rows = [_json_row(row) for row in rows]
        normalized_semantic_refs = list(semantic_refs or [])
        fingerprint = _registration_fingerprint(
            result_set_id=result_set_id,
            kind=kind,
            fields=normalized_fields,
            rows=json_rows,
            row_count=row_count,
            attempt=None if idempotency_key else attempt,
            source_sql=source_sql,
            semantic_refs=normalized_semantic_refs,
        )
        cache_key = (execution_id, idempotency_key) if idempotency_key else None
        created_at = datetime.now()
        with self._write_lock:
            if cache_key is not None:
                assert idempotency_key is not None
                cached = self._idempotent_refs.get(cache_key)
                if cached is not None:
                    cached_fingerprint, cached_ref = cached
                    if cached_fingerprint != fingerprint:
                        raise ValueError("RESULT_SET_IDEMPOTENCY_CONFLICT")
                    return cached_ref
                persisted = self._artifact_service.find_by_idempotency_key(
                    execution_id=execution_id,
                    kind=self.ARTIFACT_KIND,
                    idempotency_key=idempotency_key,
                )
                if persisted is not None:
                    persisted_fingerprint = persisted.metadata.get(
                        "registration_fingerprint"
                    )
                    if persisted_fingerprint != fingerprint:
                        raise ValueError("RESULT_SET_IDEMPOTENCY_CONFLICT")
                    ref = _result_ref_from_snapshot(persisted)
                    self._idempotent_refs[cache_key] = (fingerprint, ref)
                    return ref
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
                        "attempt": attempt,
                    },
                    metadata={
                        "result_set_id": result_set_id,
                        "plan_id": plan_id,
                        "node_id": node_id,
                        "query_id": node_id,
                        "result_kind": kind.value,
                        "row_count": row_count,
                        "attempt": attempt,
                        "source_sql": source_sql,
                        "semantic_refs": normalized_semantic_refs,
                        "created_at": created_at.isoformat(),
                        "numeric_stats": _numeric_stats(rows),
                        "registration_fingerprint": fingerprint,
                        **(
                            {"idempotency_key": idempotency_key}
                            if idempotency_key
                            else {}
                        ),
                    },
                )
            )
            ref = ResultSetRef(
                result_set_id=result_set_id,
                plan_id=plan_id,
                node_id=node_id,
                kind=kind,
                artifact_ref=artifact_ref,
                fields=tuple(normalized_fields),
                row_count=row_count,
                attempt=attempt,
                source_sql=source_sql,
                semantic_refs=tuple(normalized_semantic_refs),
                created_at=created_at,
            )
            if cache_key is not None:
                assert idempotency_key is not None
                persisted = self._artifact_service.find_by_idempotency_key(
                    execution_id=execution_id,
                    kind=self.ARTIFACT_KIND,
                    idempotency_key=idempotency_key,
                )
                if persisted is None:
                    raise ValueError("RESULT_SET_IDEMPOTENCY_RECORD_MISSING")
                if persisted.metadata.get("registration_fingerprint") != fingerprint:
                    raise ValueError("RESULT_SET_IDEMPOTENCY_CONFLICT")
                ref = _result_ref_from_snapshot(persisted)
                self._idempotent_refs[cache_key] = (fingerprint, ref)
            return ref

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
                    "attempt": ref.attempt,
                },
            )
        )
        payload = snapshot.payload
        if payload.get("result_set_id") != ref.result_set_id:
            raise ValueError("RESULT_SET_PAYLOAD_ID_MISMATCH")
        fields = payload.get("fields")
        rows = payload.get("rows")
        row_count = payload.get("row_count")
        if (
            not isinstance(fields, list)
            or not isinstance(rows, list)
            or not isinstance(row_count, int)
        ):
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


def _numeric_stats(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, int | float | str]]:
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


def _registration_fingerprint(
    *,
    result_set_id: str,
    kind: ResultSetKind,
    fields: list[str],
    rows: list[dict[str, Any]],
    row_count: int,
    attempt: int | None,
    source_sql: str | None,
    semantic_refs: list[dict[str, Any]],
) -> str:
    """为同一幂等键校验请求内容是否一致。"""

    payload = {
        "result_set_id": result_set_id,
        "kind": kind.value,
        "fields": fields,
        "rows": rows,
        "row_count": row_count,
        "attempt": attempt,
        "source_sql": source_sql,
        "semantic_refs": semantic_refs,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_ref_from_snapshot(snapshot: ResultArtifactSnapshot) -> ResultSetRef:
    """把持久化 Artifact 元数据还原为 ResultSetRef。"""

    metadata = snapshot.metadata
    return ResultSetRef(
        result_set_id=str(metadata["result_set_id"]),
        plan_id=str(metadata["plan_id"]),
        node_id=str(metadata["node_id"]),
        kind=ResultSetKind(str(metadata["result_kind"])),
        artifact_ref=ChatBIResultArtifactRef(
            artifact_id=snapshot.artifact_id,
            kind=snapshot.kind,
            content_type=snapshot.content_type,
            size=snapshot.size,
            digest=snapshot.digest,
            metadata=metadata,
        ),
        fields=tuple(str(item) for item in snapshot.payload.get("fields", [])),
        row_count=int(metadata["row_count"]),
        attempt=int(metadata["attempt"]),
        source_sql=(
            str(metadata["source_sql"])
            if metadata.get("source_sql") is not None
            else None
        ),
        semantic_refs=tuple(metadata.get("semantic_refs") or ()),
        created_at=datetime.fromisoformat(str(metadata["created_at"])),
    )


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
