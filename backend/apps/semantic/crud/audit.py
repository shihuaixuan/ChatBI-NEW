from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import select

from apps.semantic.models.semantic_model import SemanticAssetAudit
from common.core.deps import SessionDep

SENSITIVE_KEYS = {
    "authorization",
    "configuration",
    "connection",
    "connection_string",
    "dsn",
    "extraJdbc",
    "jdbc",
    "password",
    "secret",
    "token",
}


def sanitize_audit_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key in SENSITIVE_KEYS or key.lower() in SENSITIVE_KEYS:
                continue
            sanitized[key] = sanitize_audit_snapshot(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_audit_snapshot(item) for item in value]
    return deepcopy(value)


def create_audit(
    session: SessionDep,
    asset_type: str,
    asset_id: int,
    action: str,
    before: dict | None,
    after: dict | None,
    user_id: int | None,
) -> SemanticAssetAudit:
    audit = SemanticAssetAudit(
        asset_type=asset_type,
        asset_id=asset_id,
        action=action,
        before=sanitize_audit_snapshot(before),
        after=sanitize_audit_snapshot(after),
        created_at=datetime.now(),
        created_by=user_id,
    )
    session.add(audit)
    session.flush()
    session.refresh(audit)
    return audit


def list_asset_audits(session: SessionDep, asset_type: str, asset_id: int) -> list[SemanticAssetAudit]:
    statement = (
        select(SemanticAssetAudit)
        .where(SemanticAssetAudit.asset_type == asset_type, SemanticAssetAudit.asset_id == asset_id)
        .order_by(SemanticAssetAudit.created_at.desc(), SemanticAssetAudit.id.desc())
    )
    return list(session.execute(statement).scalars().all())
