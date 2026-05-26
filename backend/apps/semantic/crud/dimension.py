from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, func, or_, select

from apps.semantic.crud.audit import create_audit
from apps.semantic.models.semantic_model import (
    AssetOrigin,
    AssetStatus,
    AssetType,
    DimensionType,
    SemanticDimension,
    SemanticType,
)
from apps.semantic.models.semantic_schema import DimensionCreate, DimensionUpdate
from apps.semantic.services.expression_validator import validate_dimension_expr
from apps.semantic.services.semantic_embedding import submit_dimension_embedding_rebuild
from apps.semantic.services.semantic_observability import record_semantic_metric
from apps.semantic.services.semantic_search import invalidate_semantic_asset_cache
from apps.semantic.services.transaction_hooks import run_after_commit
from common.core.deps import SessionDep


def _dimension_snapshot(dimension: SemanticDimension) -> dict[str, Any]:
    return {
        "id": dimension.id,
        "oid": dimension.oid,
        "datasource_id": dimension.datasource_id,
        "table_id": dimension.table_id,
        "field_id": dimension.field_id,
        "name": dimension.name,
        "display_name": dimension.display_name,
        "aliases": dimension.aliases or [],
        "description": dimension.description,
        "expr": dimension.expr,
        "dimension_type": dimension.dimension_type,
        "semantic_type": dimension.semantic_type,
        "data_type": dimension.data_type,
        "time_granularities": dimension.time_granularities or [],
        "default_values": dimension.default_values or [],
        "owner_id": dimension.owner_id,
        "status": dimension.status,
        "origin": dimension.origin,
    }


def _ensure_dimension_name_available(
    session: SessionDep,
    oid: int,
    datasource_id: int,
    name: str,
    dimension_id: int | None = None,
):
    statement = select(SemanticDimension.id).where(
        SemanticDimension.oid == oid,
        SemanticDimension.datasource_id == datasource_id,
        SemanticDimension.name == name,
    )
    if dimension_id is not None:
        statement = statement.where(SemanticDimension.id != dimension_id)
    duplicated = session.execute(statement).first()
    if duplicated:
        raise ValueError("SEMANTIC_ASSET_DUPLICATED")


def _validate_dimension_type(dimension_type: str, semantic_type: str):
    if dimension_type == DimensionType.TIME.value and semantic_type not in {
        SemanticType.DATE.value,
        SemanticType.DATETIME.value,
    }:
        raise ValueError("SEMANTIC_TIME_DIMENSION_TYPE_INVALID")


def page_dimensions(
    session: SessionDep,
    oid: int,
    datasource_id: int,
    page: int,
    size: int,
    keyword: str | None,
    status: str | None,
    table_id: int | None,
    owner_id: int | None,
) -> tuple[int, int, int, list[SemanticDimension]]:
    page = max(page, 1)
    size = max(size, 1)
    statement = select(SemanticDimension).where(
        SemanticDimension.oid == oid,
        SemanticDimension.datasource_id == datasource_id,
    )

    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(
                SemanticDimension.name.ilike(pattern),
                SemanticDimension.display_name.ilike(pattern),
                cast(SemanticDimension.aliases, Text).ilike(pattern),
            )
        )
    if status:
        statement = statement.where(SemanticDimension.status == status)
    if table_id is not None:
        statement = statement.where(SemanticDimension.table_id == table_id)
    if owner_id is not None:
        statement = statement.where(SemanticDimension.owner_id == owner_id)

    total_count = session.execute(select(func.count()).select_from(statement.subquery())).scalar() or 0
    dimensions = list(
        session.execute(
            statement.order_by(SemanticDimension.updated_at.desc().nullslast(), SemanticDimension.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .scalars()
        .all()
    )
    return page, size, total_count, dimensions


def get_dimension(session: SessionDep, oid: int, dimension_id: int) -> SemanticDimension:
    dimension = session.execute(
        select(SemanticDimension).where(SemanticDimension.oid == oid, SemanticDimension.id == dimension_id)
    ).scalar_one_or_none()
    if dimension is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    return dimension


def _get_dimension_for_update(session: SessionDep, oid: int, dimension_id: int) -> SemanticDimension:
    dimension = session.execute(
        select(SemanticDimension)
        .where(SemanticDimension.oid == oid, SemanticDimension.id == dimension_id)
        .with_for_update()
    ).scalar_one_or_none()
    if dimension is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    return dimension


def create_dimension(session: SessionDep, oid: int, payload: DimensionCreate, user_id: int | None) -> SemanticDimension:
    _validate_dimension_type(payload.dimension_type, payload.semantic_type)
    _ensure_dimension_name_available(session, oid, payload.datasource_id, payload.name)
    now = datetime.now()
    dimension = SemanticDimension(
        oid=oid,
        datasource_id=payload.datasource_id,
        table_id=payload.table_id,
        field_id=payload.field_id,
        name=payload.name,
        display_name=payload.display_name,
        aliases=payload.aliases,
        description=payload.description,
        expr=payload.expr,
        dimension_type=payload.dimension_type,
        semantic_type=payload.semantic_type,
        data_type=payload.data_type,
        time_granularities=payload.time_granularities,
        default_values=payload.default_values,
        owner_id=payload.owner_id,
        status=payload.status,
        origin=AssetOrigin.MANUAL.value,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    create_audit(session, AssetType.DIMENSION.value, dimension.id, "CREATE", None, _dimension_snapshot(dimension), user_id)
    invalidate_semantic_asset_cache(oid, dimension.datasource_id)
    return dimension


def update_dimension(
    session: SessionDep,
    oid: int,
    dimension_id: int,
    payload: DimensionUpdate,
    user_id: int | None,
) -> SemanticDimension:
    dimension = get_dimension(session, oid, dimension_id)
    before = _dimension_snapshot(dimension)
    data = payload.model_dump(exclude_unset=True)

    next_dimension_type = data.get("dimension_type", dimension.dimension_type)
    next_semantic_type = data.get("semantic_type", dimension.semantic_type)
    _validate_dimension_type(next_dimension_type, next_semantic_type)

    if "name" in data and data["name"] != dimension.name:
        _ensure_dimension_name_available(session, oid, dimension.datasource_id, data["name"], dimension_id=dimension.id)

    for key, value in data.items():
        setattr(dimension, key, value)
    dimension.updated_at = datetime.now()
    dimension.updated_by = user_id
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    create_audit(session, AssetType.DIMENSION.value, dimension.id, "UPDATE", before, _dimension_snapshot(dimension), user_id)
    invalidate_semantic_asset_cache(oid, dimension.datasource_id)
    return dimension


def approve_dimension(session: SessionDep, oid: int, dimension_id: int, user_id: int | None) -> SemanticDimension:
    dimension = _get_dimension_for_update(session, oid, dimension_id)
    if dimension.status not in {AssetStatus.CANDIDATE.value, AssetStatus.DISABLED.value}:
        raise ValueError("SEMANTIC_STATUS_INVALID")
    if dimension.table_id is not None:
        validation = validate_dimension_expr(session, dimension)
        if not validation.valid:
            record_semantic_metric(
                "semantic_expr_validate_failed_total",
                asset_type=AssetType.DIMENSION.value,
                asset_id=dimension.id,
                datasource_id=dimension.datasource_id,
                expr=dimension.expr,
                error=validation.error,
            )
            raise ValueError(f"SEMANTIC_EXPR_INVALID: {validation.error}")
    before = _dimension_snapshot(dimension)
    dimension.status = AssetStatus.APPROVED.value
    dimension.updated_at = datetime.now()
    dimension.updated_by = user_id
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    create_audit(session, AssetType.DIMENSION.value, dimension.id, "APPROVE", before, _dimension_snapshot(dimension), user_id)
    invalidate_semantic_asset_cache(oid, dimension.datasource_id)
    record_semantic_metric(
        "semantic_asset_approved_total",
        asset_type=AssetType.DIMENSION.value,
        asset_id=dimension.id,
        datasource_id=dimension.datasource_id,
    )
    run_after_commit(session, lambda dimension_id=dimension.id: submit_dimension_embedding_rebuild(dimension_id))
    return dimension


def disable_dimension(session: SessionDep, oid: int, dimension_id: int, reason: str, user_id: int | None) -> SemanticDimension:
    dimension = get_dimension(session, oid, dimension_id)
    if dimension.status == AssetStatus.DISABLED.value:
        return dimension
    before = _dimension_snapshot(dimension)
    dimension.status = AssetStatus.DISABLED.value
    dimension.updated_at = datetime.now()
    dimension.updated_by = user_id
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    after = _dimension_snapshot(dimension)
    after["disable_reason"] = reason
    create_audit(session, AssetType.DIMENSION.value, dimension.id, "DISABLE", before, after, user_id)
    invalidate_semantic_asset_cache(oid, dimension.datasource_id)
    return dimension
