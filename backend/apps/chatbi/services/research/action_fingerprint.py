"""Research ReAct 动作的确定性指纹。

指纹只由影响执行结果的动作参数、语义版本、Scope 和权限版本组成。模型用于
解释动作的 ``purpose`` 不属于执行语义，因此无论目的文本如何变化，都不能导致
同一动作再次执行。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from apps.chatbi.models.dto.research_agent import (
    ResearchActionType,
    ResearchVersionSnapshot,
)

FINGERPRINT_ALGORITHM_VERSION = 1

_ACTION_PREFIXES = {
    ResearchActionType.QUERY_SEMANTIC_DATA.value: "query",
    ResearchActionType.COMPUTE_EVIDENCE.value: "compute",
    ResearchActionType.READ_EVIDENCE_ROWS.value: "read",
    ResearchActionType.SEARCH_SEMANTIC_ASSETS.value: "search",
    ResearchActionType.REQUEST_CLARIFICATION.value: "clarification",
    ResearchActionType.FINISH_RESEARCH.value: "finish",
}


def research_action_fingerprint(
    action: BaseModel | Mapping[str, Any],
    *,
    version_snapshot: ResearchVersionSnapshot | Mapping[str, Any] | None = None,
    semantic_version: str = "research-semantic-v1",
    permission_version: str = "research-permission-v1",
    scope_fingerprint: str = "research-scope-v1",
) -> str:
    """生成六类 Research 动作共用的稳定指纹。

    ``version_snapshot`` 存在时优先使用冻结快照中的版本；调用方也可以在尚未
    组装完整 Requirement 的单元测试中显式传入三个版本字符串。
    """

    payload = _model_dump(action)
    action_type = _action_type(payload)
    arguments = payload.get("arguments")
    if not isinstance(arguments, Mapping):
        raise ValueError("RESEARCH_ACTION_ARGUMENTS_REQUIRED")

    versions = _version_payload(
        version_snapshot,
        semantic_version=semantic_version,
        permission_version=permission_version,
        scope_fingerprint=scope_fingerprint,
    )
    canonical_payload = {
        "algorithm_version": FINGERPRINT_ALGORITHM_VERSION,
        "action_type": action_type,
        "arguments": _without_purpose(arguments),
        "versions": versions,
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"{_ACTION_PREFIXES.get(action_type, 'action')}_{digest}"


def build_research_action_fingerprint(
    action: BaseModel | Mapping[str, Any],
    *,
    version_snapshot: ResearchVersionSnapshot | Mapping[str, Any] | None = None,
    semantic_version: str = "research-semantic-v1",
    permission_version: str = "research-permission-v1",
    scope_fingerprint: str = "research-scope-v1",
) -> str:
    """``research_action_fingerprint`` 的语义化别名。"""

    return research_action_fingerprint(
        action,
        version_snapshot=version_snapshot,
        semantic_version=semantic_version,
        permission_version=permission_version,
        scope_fingerprint=scope_fingerprint,
    )


def _model_dump(action: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(action, BaseModel):
        return action.model_dump(mode="json")
    return dict(action)


def _action_type(payload: Mapping[str, Any]) -> str:
    value = payload.get("action_type")
    if isinstance(value, ResearchActionType):
        return value.value
    if isinstance(value, str) and value in _ACTION_PREFIXES:
        return value
    raise ValueError("RESEARCH_ACTION_TYPE_INVALID")


def _version_payload(
    snapshot: ResearchVersionSnapshot | Mapping[str, Any] | None,
    *,
    semantic_version: str,
    permission_version: str,
    scope_fingerprint: str,
) -> dict[str, Any]:
    if snapshot is not None:
        snapshot_payload = ResearchVersionSnapshot.model_validate(snapshot).model_dump(
            mode="json"
        )
        return {
            "schema_version": snapshot_payload.get("schema_version"),
            "contract_version": snapshot_payload.get("contract_version"),
            "schema_fingerprint": snapshot_payload.get("schema_fingerprint"),
            "scope_fingerprint": snapshot_payload.get("scope_fingerprint"),
            "permission_fingerprint": snapshot_payload.get("permission_fingerprint"),
        }
    for name, version_value in (
        ("semantic_version", semantic_version),
        ("permission_version", permission_version),
        ("scope_fingerprint", scope_fingerprint),
    ):
        if not isinstance(version_value, str) or not version_value.strip():
            raise ValueError(f"RESEARCH_ACTION_{name.upper()}_REQUIRED")
    return {
        "semantic_version": semantic_version,
        "scope_fingerprint": scope_fingerprint,
        "permission_version": permission_version,
    }


def _without_purpose(value: Any) -> Any:
    """只删除动作说明字段，保留嵌套参数中的业务文本值。"""

    if isinstance(value, Mapping):
        return {
            str(key): _without_purpose(child)
            for key, child in value.items()
            if key != "purpose"
        }
    if isinstance(value, tuple):
        return [_without_purpose(item) for item in value]
    if isinstance(value, list):
        return [_without_purpose(item) for item in value]
    return value


__all__ = [
    "FINGERPRINT_ALGORITHM_VERSION",
    "build_research_action_fingerprint",
    "research_action_fingerprint",
]
