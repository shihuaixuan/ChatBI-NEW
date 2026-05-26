from datetime import datetime
from typing import Any

from sqlalchemy import select

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.semantic.models.semantic_model import ChatRecordSemanticAsset, UsageRole
from apps.semantic.models.semantic_schema import SemanticAssetMatch
from common.core.deps import SessionDep

DEFAULT_USAGE_ROLES = [UsageRole.MATCHED.value, UsageRole.INJECTED.value, UsageRole.USED.value]


def _match_value(match: SemanticAssetMatch | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(match, dict):
        return match.get(key, default)
    return getattr(match, key, default)


def _roles_for_match(match: SemanticAssetMatch | dict[str, Any]) -> list[str]:
    role = _match_value(match, "role")
    if role:
        return [role]
    roles = _match_value(match, "roles")
    if roles:
        return list(roles)
    return DEFAULT_USAGE_ROLES


def save_chat_asset_usage(session: SessionDep, record_id: int, matches: list[SemanticAssetMatch | dict[str, Any]]) -> None:
    now = datetime.now()
    for match in matches:
        for role in _roles_for_match(match):
            asset_type = _match_value(match, "asset_type")
            asset_id = _match_value(match, "asset_id")
            usage = session.execute(
                select(ChatRecordSemanticAsset).where(
                    ChatRecordSemanticAsset.record_id == record_id,
                    ChatRecordSemanticAsset.asset_type == asset_type,
                    ChatRecordSemanticAsset.asset_id == asset_id,
                    ChatRecordSemanticAsset.role == role,
                )
            ).scalar_one_or_none()

            if usage is None:
                usage = ChatRecordSemanticAsset(
                    record_id=record_id,
                    asset_type=asset_type,
                    asset_id=asset_id,
                    role=role,
                    created_at=now,
                )

            usage.match_word = _match_value(match, "match_word")
            usage.score = _match_value(match, "score")
            usage.snapshot = _match_value(match, "snapshot", {}) or {}
            session.add(usage)
    session.flush()


def list_chat_record_assets(session: SessionDep, oid: int, record_id: int) -> list[ChatRecordSemanticAsset]:
    record_belongs_to_oid = session.execute(
        select(ChatRecord.id)
        .join(Chat, Chat.id == ChatRecord.chat_id)
        .where(ChatRecord.id == record_id, Chat.oid == oid)
    ).first()
    if record_belongs_to_oid is None:
        raise ValueError("SEMANTIC_PERMISSION_DENIED")

    return list(
        session.execute(
            select(ChatRecordSemanticAsset)
            .where(ChatRecordSemanticAsset.record_id == record_id)
            .order_by(ChatRecordSemanticAsset.created_at.asc(), ChatRecordSemanticAsset.id.asc())
        )
        .scalars()
        .all()
    )
