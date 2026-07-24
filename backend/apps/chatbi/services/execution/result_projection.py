from __future__ import annotations

import base64
import math
from typing import Any

import orjson

from apps.chatbi.errors import QueryResultProjectionError
from apps.chatbi.models import QueryResultProjectionData
from apps.conversation import ChatRecordResultProjection, ChatRecordService

_LARGE_INTEGER_THRESHOLD = 10**15
_LARGE_FLOAT_THRESHOLD = 1e10
_SMALL_FLOAT_THRESHOLD = 1e-6


class QueryResultProjectionService:
    """统一旧 Chat 查询结果的展示标准化和 ChatRecord 投影。"""

    def __init__(self, chat_record_service: ChatRecordService) -> None:
        self._chat_record_service = chat_record_service

    def project(self, data: QueryResultProjectionData) -> dict[str, Any]:
        self._validate(data)
        normalized_rows = [
            _normalize_qualified_keys(_normalize_value(row)) for row in data.rows
        ]
        result: dict[str, Any] = {
            **_normalize_value(data.execution_metadata),
            "fields": list(data.fields),
            "data": normalized_rows,
        }
        if normalized_rows:
            if data.enable_row_limit and len(normalized_rows) > data.row_limit:
                result["data"] = normalized_rows[: data.row_limit]
                result["limit"] = data.row_limit
            result["datasource"] = data.datasource_id

        try:
            serialized = orjson.dumps(result).decode()
        except TypeError as exc:
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_SERIALIZATION_FAILED"
            ) from exc
        self._chat_record_service.project_result_by_id(
            data.record_id,
            ChatRecordResultProjection(data=serialized),
        )
        return result

    @staticmethod
    def _validate(data: QueryResultProjectionData) -> None:
        if (
            isinstance(data.record_id, bool)
            or not isinstance(data.record_id, int)
            or data.record_id <= 0
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_RECORD_ID_INVALID"
            )
        if (
            isinstance(data.datasource_id, bool)
            or not isinstance(data.datasource_id, int)
            or data.datasource_id <= 0
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_DATASOURCE_ID_INVALID"
            )
        if not isinstance(data.fields, list) or any(
            not isinstance(field, str) or not field.strip()
            for field in data.fields
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_FIELDS_INVALID"
            )
        if not isinstance(data.rows, list) or any(
            not isinstance(row, dict) or _contains_non_string_key(row)
            for row in data.rows
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_ROWS_INVALID"
            )
        if not isinstance(data.execution_metadata, dict) or (
            _contains_non_string_key(data.execution_metadata)
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_METADATA_INVALID"
            )
        if not isinstance(data.enable_row_limit, bool):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_ROW_LIMIT_FLAG_INVALID"
            )
        if (
            isinstance(data.row_limit, bool)
            or not isinstance(data.row_limit, int)
            or data.row_limit <= 0
        ):
            raise QueryResultProjectionError(
                "QUERY_RESULT_PROJECTION_ROW_LIMIT_INVALID"
            )


def _normalize_value(value: Any) -> Any:
    """递归处理大数和旧查询结果中的 bytes 值。"""

    if isinstance(value, bytes):
        return base64.b64encode(value).decode("utf-8")
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) >= _LARGE_INTEGER_THRESHOLD:
        return str(value)
    if isinstance(value, float) and (
        abs(value) >= _LARGE_FLOAT_THRESHOLD
        or abs(value) < _SMALL_FLOAT_THRESHOLD
    ):
        return _format_float_without_scientific(value)
    return value


def _format_float_without_scientific(value: float) -> str:
    if value == 0:
        return "0"
    if not math.isfinite(value):
        return str(value)
    formatted = f"{value:.15f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _normalize_qualified_keys(row: dict[str, Any]) -> dict[str, Any]:
    """为 alias.column 补充短字段名，已有短字段时不覆盖。"""

    normalized = dict(row)
    for key, value in row.items():
        if "." not in key:
            continue
        short_key = key.rsplit(".", 1)[-1]
        if short_key not in normalized:
            normalized[short_key] = value
    return normalized


def _contains_non_string_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            not isinstance(key, str) or _contains_non_string_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_non_string_key(item) for item in value)
    return False


__all__ = [
    "QueryResultProjectionError",
    "QueryResultProjectionService",
]
