from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, func, or_, select

from apps.semantic.crud.audit import create_audit
from apps.semantic.models.semantic_model import (
    AssetOrigin,
    AssetStatus,
    AssetType,
    SemanticMetric,
)
from apps.semantic.models.semantic_schema import MetricCreate, MetricUpdate
from apps.semantic.services.expression_validator import validate_metric_expr
from apps.semantic.services.semantic_embedding import submit_metric_embedding_rebuild
from apps.semantic.services.semantic_observability import record_semantic_metric
from apps.semantic.services.semantic_search import invalidate_semantic_asset_cache
from apps.semantic.services.transaction_hooks import run_after_commit
from common.core.deps import SessionDep


def _metric_snapshot(metric: SemanticMetric) -> dict[str, Any]:
    return {
        "id": metric.id,
        "oid": metric.oid,
        "datasource_id": metric.datasource_id,
        "table_id": metric.table_id,
        "field_id": metric.field_id,
        "name": metric.name,
        "display_name": metric.display_name,
        "aliases": metric.aliases or [],
        "description": metric.description,
        "define_type": metric.define_type,
        "expr": metric.expr,
        "default_agg": metric.default_agg,
        "filter_sql": metric.filter_sql,
        "data_type": metric.data_type,
        "data_format": metric.data_format,
        "default_time_dimension_id": metric.default_time_dimension_id,
        "related_dimension_ids": metric.related_dimension_ids or [],
        "owner_id": metric.owner_id,
        "status": metric.status,
        "origin": metric.origin,
    }


def _ensure_metric_name_available(session: SessionDep, oid: int, datasource_id: int, name: str, metric_id: int | None = None):
    statement = select(SemanticMetric.id).where(
        SemanticMetric.oid == oid,
        SemanticMetric.datasource_id == datasource_id,
        SemanticMetric.name == name,
    )
    if metric_id is not None:
        statement = statement.where(SemanticMetric.id != metric_id)
    duplicated = session.execute(statement).first()
    if duplicated:
        raise ValueError("SEMANTIC_ASSET_DUPLICATED")


def page_metrics(
    session: SessionDep,
    oid: int,
    datasource_id: int,
    page: int,
    size: int,
    keyword: str | None,
    status: str | None,
    table_id: int | None,
    owner_id: int | None,
) -> tuple[int, int, int, list[SemanticMetric]]:
    page = max(page, 1)
    size = max(size, 1)
    statement = select(SemanticMetric).where(SemanticMetric.oid == oid, SemanticMetric.datasource_id == datasource_id)

    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(
                SemanticMetric.name.ilike(pattern),
                SemanticMetric.display_name.ilike(pattern),
                cast(SemanticMetric.aliases, Text).ilike(pattern),
            )
        )
    if status:
        statement = statement.where(SemanticMetric.status == status)
    if table_id is not None:
        statement = statement.where(SemanticMetric.table_id == table_id)
    if owner_id is not None:
        statement = statement.where(SemanticMetric.owner_id == owner_id)

    total_count = session.execute(select(func.count()).select_from(statement.subquery())).scalar() or 0
    metrics = list(
        session.execute(
            statement.order_by(SemanticMetric.updated_at.desc().nullslast(), SemanticMetric.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .scalars()
        .all()
    )
    return page, size, total_count, metrics


def get_metric(session: SessionDep, oid: int, metric_id: int) -> SemanticMetric:
    metric = session.execute(
        select(SemanticMetric).where(SemanticMetric.oid == oid, SemanticMetric.id == metric_id)
    ).scalar_one_or_none()
    if metric is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    return metric


def _get_metric_for_update(session: SessionDep, oid: int, metric_id: int) -> SemanticMetric:
    metric = session.execute(
        select(SemanticMetric)
        .where(SemanticMetric.oid == oid, SemanticMetric.id == metric_id)
        .with_for_update()
    ).scalar_one_or_none()
    if metric is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    return metric


def create_metric(session: SessionDep, oid: int, payload: MetricCreate, user_id: int | None) -> SemanticMetric:
    _ensure_metric_name_available(session, oid, payload.datasource_id, payload.name)
    now = datetime.now()
    metric = SemanticMetric(
        oid=oid,
        datasource_id=payload.datasource_id,
        table_id=payload.table_id,
        field_id=payload.field_id,
        name=payload.name,
        display_name=payload.display_name,
        aliases=payload.aliases,
        description=payload.description,
        define_type=payload.define_type,
        expr=payload.expr,
        default_agg=payload.default_agg,
        filter_sql=payload.filter_sql,
        data_type=payload.data_type,
        data_format=payload.data_format,
        default_time_dimension_id=payload.default_time_dimension_id,
        related_dimension_ids=payload.related_dimension_ids,
        owner_id=payload.owner_id,
        status=payload.status,
        origin=AssetOrigin.MANUAL.value,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(metric)
    session.flush()
    session.refresh(metric)
    create_audit(session, AssetType.METRIC.value, metric.id, "CREATE", None, _metric_snapshot(metric), user_id)
    invalidate_semantic_asset_cache(oid, metric.datasource_id)
    return metric


def update_metric(session: SessionDep, oid: int, metric_id: int, payload: MetricUpdate, user_id: int | None) -> SemanticMetric:
    metric = get_metric(session, oid, metric_id)
    before = _metric_snapshot(metric)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != metric.name:
        _ensure_metric_name_available(session, oid, metric.datasource_id, data["name"], metric_id=metric.id)

    for key, value in data.items():
        setattr(metric, key, value)
    metric.updated_at = datetime.now()
    metric.updated_by = user_id
    session.add(metric)
    session.flush()
    session.refresh(metric)
    create_audit(session, AssetType.METRIC.value, metric.id, "UPDATE", before, _metric_snapshot(metric), user_id)
    invalidate_semantic_asset_cache(oid, metric.datasource_id)
    if before["status"] == AssetStatus.APPROVED.value:
        run_after_commit(session, lambda metric_id=metric.id: submit_metric_embedding_rebuild(metric_id))
    return metric


def approve_metric(session: SessionDep, oid: int, metric_id: int, user_id: int | None) -> SemanticMetric:
    metric = _get_metric_for_update(session, oid, metric_id)
    if metric.status not in {AssetStatus.CANDIDATE.value, AssetStatus.DISABLED.value}:
        raise ValueError("SEMANTIC_STATUS_INVALID")
    if metric.table_id is not None:
        validation = validate_metric_expr(session, metric)
        if not validation.valid:
            record_semantic_metric(
                "semantic_expr_validate_failed_total",
                asset_type=AssetType.METRIC.value,
                asset_id=metric.id,
                datasource_id=metric.datasource_id,
                expr=metric.expr,
                error=validation.error,
            )
            raise ValueError(f"SEMANTIC_EXPR_INVALID: {validation.error}")
    before = _metric_snapshot(metric)
    metric.status = AssetStatus.APPROVED.value
    metric.updated_at = datetime.now()
    metric.updated_by = user_id
    session.add(metric)
    session.flush()
    session.refresh(metric)
    create_audit(session, AssetType.METRIC.value, metric.id, "APPROVE", before, _metric_snapshot(metric), user_id)
    invalidate_semantic_asset_cache(oid, metric.datasource_id)
    record_semantic_metric(
        "semantic_asset_approved_total",
        asset_type=AssetType.METRIC.value,
        asset_id=metric.id,
        datasource_id=metric.datasource_id,
    )
    run_after_commit(session, lambda metric_id=metric.id: submit_metric_embedding_rebuild(metric_id))
    return metric


def disable_metric(session: SessionDep, oid: int, metric_id: int, reason: str, user_id: int | None) -> SemanticMetric:
    metric = get_metric(session, oid, metric_id)
    if metric.status == AssetStatus.DISABLED.value:
        return metric
    before = _metric_snapshot(metric)
    metric.status = AssetStatus.DISABLED.value
    metric.updated_at = datetime.now()
    metric.updated_by = user_id
    session.add(metric)
    session.flush()
    session.refresh(metric)
    after = _metric_snapshot(metric)
    after["disable_reason"] = reason
    create_audit(session, AssetType.METRIC.value, metric.id, "DISABLE", before, after, user_id)
    invalidate_semantic_asset_cache(oid, metric.datasource_id)
    return metric
