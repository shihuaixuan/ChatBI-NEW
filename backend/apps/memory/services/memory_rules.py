"""用户记忆规则纯函数。"""

import json
import re
import unicodedata
from typing import Any

from apps.memory.models.dto import MemoryEvidenceType

_FORBIDDEN_PAYLOAD_KEYS = {
    "dataset_id",
    "datasource_id",
    "metric_id",
    "dimension_id",
    "table",
    "field",
    "sql",
}


def normalize_memory_key(value: str) -> str:
    """统一用户记忆键，避免同一偏好因空白和大小写重复保存。"""

    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"\s+", "_", normalized)
    if not normalized:
        raise ValueError("MEMORY_KEY_EMPTY")
    return normalized[:160]


def payload_fingerprint(payload: dict[str, Any]) -> str:
    """生成可比较的结构化载荷指纹。"""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_memory_payload(payload: dict[str, Any]) -> None:
    """拒绝把数据资产绑定、SQL 或查询范围写入用户记忆。"""

    pending: list[Any] = [payload]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            forbidden = {
                str(key).casefold()
                for key in current
                if str(key).casefold() in _FORBIDDEN_PAYLOAD_KEYS
            }
            if forbidden:
                raise ValueError(
                    f"MEMORY_PAYLOAD_FORBIDDEN_FIELDS:{','.join(sorted(forbidden))}"
                )
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def evidence_strength(evidence_type: MemoryEvidenceType) -> float:
    """返回证据来源强度。"""

    return {
        MemoryEvidenceType.MANUAL_EDIT: 1.0,
        MemoryEvidenceType.EXPLICIT_CORRECTION: 1.0,
        MemoryEvidenceType.USER_SETTING: 1.0,
        MemoryEvidenceType.EXPLICIT_CONFIRMATION: 0.95,
        MemoryEvidenceType.REPEATED_BEHAVIOR: 0.75,
        MemoryEvidenceType.SUCCESSFUL_QUERY: 0.55,
    }[evidence_type]
