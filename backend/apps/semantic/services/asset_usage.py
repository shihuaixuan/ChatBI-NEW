import re
from typing import Any

from apps.semantic.crud.usage import save_chat_asset_usage
from apps.semantic.models.semantic_model import UsageRole
from apps.semantic.models.semantic_schema import SemanticAssetMatch
from common.core.deps import SessionDep


def _match_dump(match: SemanticAssetMatch | dict[str, Any]) -> dict[str, Any]:
    if isinstance(match, SemanticAssetMatch):
        return match.model_dump(mode="json")
    return dict(match)


def _match_with_roles(match: SemanticAssetMatch | dict[str, Any], roles: list[str]) -> dict[str, Any]:
    data = _match_dump(match)
    data["roles"] = roles
    return data


def _normalize_sql_fragment(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.lower().replace('"', "").replace("`", "").replace("[", "").replace("]", ""))


def _asset_used_in_sql(match: SemanticAssetMatch | dict[str, Any], sql: str) -> bool:
    data = _match_dump(match)
    snapshot = data.get("snapshot") or {}
    normalized_sql = _normalize_sql_fragment(sql)
    for candidate in (snapshot.get("expr"), snapshot.get("name"), data.get("name")):
        normalized_candidate = _normalize_sql_fragment(candidate)
        if normalized_candidate and normalized_candidate in normalized_sql:
            return True
    return False


def save_matched_and_injected_semantic_assets(
    session: SessionDep,
    record_id: int,
    matches: list[SemanticAssetMatch | dict[str, Any]],
    semantic_context: str,
) -> None:
    if not matches:
        return
    roles = [UsageRole.MATCHED.value]
    if semantic_context:
        roles.append(UsageRole.INJECTED.value)
    save_chat_asset_usage(session, record_id, [_match_with_roles(match, roles) for match in matches])


def save_used_semantic_assets(
    session: SessionDep,
    record_id: int,
    matches: list[SemanticAssetMatch | dict[str, Any]],
    sql: str,
) -> None:
    used_matches = [
        _match_with_roles(match, [UsageRole.USED.value])
        for match in matches
        if _asset_used_in_sql(match, sql)
    ]
    if not used_matches:
        return
    save_chat_asset_usage(session, record_id, used_matches)
