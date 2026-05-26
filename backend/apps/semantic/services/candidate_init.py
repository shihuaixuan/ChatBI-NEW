from dataclasses import dataclass, field

from sqlalchemy import select

from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.crud.audit import create_audit
from apps.semantic.models.semantic_model import (
    AggregationType,
    AssetOrigin,
    AssetStatus,
    AssetType,
    DimensionType,
    SemanticDimension,
    SemanticMetric,
    SemanticType,
)
from apps.semantic.models.semantic_schema import InitializeSemanticResponse
from apps.semantic.services.semantic_observability import record_semantic_metric
from common.core.deps import SessionDep

NUMERIC_TYPES = {
    "bigint",
    "decimal",
    "double",
    "float",
    "int",
    "integer",
    "number",
    "numeric",
    "real",
    "smallint",
}
DATE_TYPES = {"date"}
DATETIME_TYPES = {"datetime", "timestamp", "timestamptz", "time"}
TEXT_TYPES = {"char", "character", "string", "text", "varchar"}

AMOUNT_KEYWORDS = {"amount", "price", "gmv", "revenue"}
RATE_KEYWORDS = {"rate", "ratio", "percent"}
ID_KEYWORDS = {"order_id", "user_id", "customer_id"}
REGION_KEYWORDS = {"region", "province", "city"}
CHANNEL_KEYWORDS = {"channel", "source", "platform"}


@dataclass
class CandidateAsset:
    asset_type: str
    name: str
    display_name: str
    expr: str
    origin: str = AssetOrigin.FIELD_INIT.value
    status: str = AssetStatus.CANDIDATE.value
    table_id: int | None = None
    field_id: int | None = None
    data_type: str | None = None
    default_agg: str | None = None
    data_format: str | None = None
    dimension_type: str | None = None
    semantic_type: str | None = None
    time_granularities: list[str] = field(default_factory=list)


def _normalized_type(field_type: str | None) -> str:
    field_type = (field_type or "").lower().strip()
    return field_type.split("(", 1)[0].strip()


def _contains_any(value: str, keywords: set[str]) -> bool:
    return any(keyword in value for keyword in keywords)


def _display_name(field: CoreField) -> str:
    return (field.custom_comment or field.field_comment or field.field_name).strip()


def _base_candidate(field: CoreField, table: CoreTable, asset_type: str) -> CandidateAsset:
    return CandidateAsset(
        asset_type=asset_type,
        name=field.field_name,
        display_name=_display_name(field),
        expr=field.field_name,
        table_id=table.id,
        field_id=field.id,
        data_type=field.field_type,
    )


def infer_semantic_asset(field: CoreField, table: CoreTable) -> CandidateAsset | None:
    field_name = (field.field_name or "").lower()
    field_type = _normalized_type(field.field_type)

    if _contains_any(field_name, ID_KEYWORDS):
        candidate = _base_candidate(field, table, AssetType.DIMENSION.value)
        candidate.dimension_type = DimensionType.ID.value
        candidate.semantic_type = SemanticType.USER.value if "user_id" in field_name else SemanticType.UNKNOWN.value
        return candidate

    if field_type in DATE_TYPES or field_type in DATETIME_TYPES:
        candidate = _base_candidate(field, table, AssetType.DIMENSION.value)
        candidate.dimension_type = DimensionType.TIME.value
        candidate.semantic_type = SemanticType.DATE.value if field_type in DATE_TYPES else SemanticType.DATETIME.value
        candidate.time_granularities = ["day", "week", "month", "quarter", "year"]
        return candidate

    if _contains_any(field_name, REGION_KEYWORDS):
        candidate = _base_candidate(field, table, AssetType.DIMENSION.value)
        candidate.dimension_type = DimensionType.GEO.value
        candidate.semantic_type = SemanticType.REGION.value
        return candidate

    if _contains_any(field_name, CHANNEL_KEYWORDS):
        candidate = _base_candidate(field, table, AssetType.DIMENSION.value)
        candidate.dimension_type = DimensionType.CATEGORY.value
        candidate.semantic_type = SemanticType.CHANNEL.value
        return candidate

    if field_type in NUMERIC_TYPES:
        if _contains_any(field_name, AMOUNT_KEYWORDS):
            candidate = _base_candidate(field, table, AssetType.METRIC.value)
            candidate.default_agg = AggregationType.SUM.value
            candidate.data_format = "currency"
            return candidate
        if _contains_any(field_name, RATE_KEYWORDS):
            candidate = _base_candidate(field, table, AssetType.METRIC.value)
            candidate.default_agg = AggregationType.AVG.value
            candidate.data_format = "percent"
            return candidate

    if field_type in TEXT_TYPES:
        candidate = _base_candidate(field, table, AssetType.DIMENSION.value)
        candidate.dimension_type = DimensionType.CATEGORY.value
        candidate.semantic_type = SemanticType.UNKNOWN.value
        return candidate

    return None


def _metric_snapshot(metric: SemanticMetric) -> dict:
    return {
        "id": metric.id,
        "name": metric.name,
        "display_name": metric.display_name,
        "expr": metric.expr,
        "default_agg": metric.default_agg,
        "data_format": metric.data_format,
        "status": metric.status,
        "origin": metric.origin,
        "table_id": metric.table_id,
        "field_id": metric.field_id,
    }


def _dimension_snapshot(dimension: SemanticDimension) -> dict:
    return {
        "id": dimension.id,
        "name": dimension.name,
        "display_name": dimension.display_name,
        "expr": dimension.expr,
        "dimension_type": dimension.dimension_type,
        "semantic_type": dimension.semantic_type,
        "time_granularities": dimension.time_granularities or [],
        "status": dimension.status,
        "origin": dimension.origin,
        "table_id": dimension.table_id,
        "field_id": dimension.field_id,
    }


@dataclass
class ExistingCandidate:
    asset: SemanticMetric | SemanticDimension
    matched_by_source: bool


def _find_existing_candidate(session: SessionDep, oid: int, datasource_id: int, candidate: CandidateAsset) -> ExistingCandidate | None:
    model = SemanticMetric if candidate.asset_type == AssetType.METRIC.value else SemanticDimension
    source_match = session.execute(
        select(model).where(
            model.oid == oid,
            model.datasource_id == datasource_id,
            model.field_id == candidate.field_id,
            model.origin == AssetOrigin.FIELD_INIT.value,
        )
    ).scalar_one_or_none()
    if source_match is not None:
        return ExistingCandidate(asset=source_match, matched_by_source=True)

    name_match = session.execute(
        select(model).where(
            model.oid == oid,
            model.datasource_id == datasource_id,
            model.name == candidate.name,
        )
    ).scalar_one_or_none()
    if name_match is not None:
        return ExistingCandidate(asset=name_match, matched_by_source=False)

    return None


def _create_metric_from_candidate(session: SessionDep, oid: int, datasource_id: int, candidate: CandidateAsset, user_id: int | None):
    metric = SemanticMetric(
        oid=oid,
        datasource_id=datasource_id,
        table_id=candidate.table_id,
        field_id=candidate.field_id,
        name=candidate.name,
        display_name=candidate.display_name,
        aliases=[],
        expr=candidate.expr,
        default_agg=candidate.default_agg or AggregationType.SUM.value,
        data_type=candidate.data_type,
        data_format=candidate.data_format,
        status=candidate.status,
        origin=candidate.origin,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(metric)
    session.flush()
    session.refresh(metric)
    create_audit(session, AssetType.METRIC.value, metric.id, "CREATE", None, _metric_snapshot(metric), user_id)
    return metric


def _create_dimension_from_candidate(session: SessionDep, oid: int, datasource_id: int, candidate: CandidateAsset, user_id: int | None):
    dimension = SemanticDimension(
        oid=oid,
        datasource_id=datasource_id,
        table_id=candidate.table_id,
        field_id=candidate.field_id,
        name=candidate.name,
        display_name=candidate.display_name,
        aliases=[],
        expr=candidate.expr,
        dimension_type=candidate.dimension_type or DimensionType.CATEGORY.value,
        semantic_type=candidate.semantic_type or SemanticType.UNKNOWN.value,
        data_type=candidate.data_type,
        time_granularities=candidate.time_granularities,
        status=candidate.status,
        origin=candidate.origin,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    create_audit(session, AssetType.DIMENSION.value, dimension.id, "CREATE", None, _dimension_snapshot(dimension), user_id)
    return dimension


def _update_metric_candidate(session: SessionDep, metric: SemanticMetric, candidate: CandidateAsset, user_id: int | None):
    before = _metric_snapshot(metric)
    metric.name = candidate.name
    metric.display_name = candidate.display_name
    metric.expr = candidate.expr
    metric.default_agg = candidate.default_agg or metric.default_agg
    metric.data_type = candidate.data_type
    metric.data_format = candidate.data_format
    metric.updated_by = user_id
    session.add(metric)
    session.flush()
    session.refresh(metric)
    create_audit(session, AssetType.METRIC.value, metric.id, "UPDATE_CANDIDATE", before, _metric_snapshot(metric), user_id)


def _update_dimension_candidate(session: SessionDep, dimension: SemanticDimension, candidate: CandidateAsset, user_id: int | None):
    before = _dimension_snapshot(dimension)
    dimension.name = candidate.name
    dimension.display_name = candidate.display_name
    dimension.expr = candidate.expr
    dimension.dimension_type = candidate.dimension_type or dimension.dimension_type
    dimension.semantic_type = candidate.semantic_type or dimension.semantic_type
    dimension.data_type = candidate.data_type
    dimension.time_granularities = candidate.time_granularities
    dimension.updated_by = user_id
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    create_audit(
        session,
        AssetType.DIMENSION.value,
        dimension.id,
        "UPDATE_CANDIDATE",
        before,
        _dimension_snapshot(dimension),
        user_id,
    )


def _checked_fields_query(oid: int, datasource_id: int, table_ids: list[int]):
    del oid
    statement = (
        select(CoreField, CoreTable)
        .join(CoreTable, CoreTable.id == CoreField.table_id)
        .where(
            CoreTable.ds_id == datasource_id,
            CoreField.ds_id == datasource_id,
            CoreTable.checked.is_(True),
            CoreField.checked.is_(True),
        )
    )
    if table_ids:
        statement = statement.where(CoreTable.id.in_(table_ids))
    return statement


def initialize_candidates(
    session: SessionDep,
    oid: int,
    datasource_id: int,
    table_ids: list[int],
    overwrite_candidate: bool,
    user_id: int | None,
    dry_run: bool,
) -> InitializeSemanticResponse:
    stats = InitializeSemanticResponse(datasource_id=datasource_id)
    record_semantic_metric(
        "semantic_candidate_init_total",
        phase="start",
        oid=oid,
        datasource_id=datasource_id,
        table_count=len(table_ids or []),
        dry_run=dry_run,
    )
    rows = session.execute(_checked_fields_query(oid, datasource_id, table_ids)).all()

    for field_obj, table in rows:
        candidate = infer_semantic_asset(field_obj, table)
        if candidate is None:
            stats.skipped += 1
            continue

        existing_candidate = _find_existing_candidate(session, oid, datasource_id, candidate)
        existing = existing_candidate.asset if existing_candidate is not None else None
        if existing is not None and (not existing_candidate.matched_by_source or existing.status == AssetStatus.APPROVED.value):
            stats.skipped += 1
            continue
        if existing is not None and not overwrite_candidate:
            stats.skipped += 1
            continue

        if candidate.asset_type == AssetType.METRIC.value:
            if dry_run:
                if existing is None:
                    stats.created_metrics += 1
                elif overwrite_candidate:
                    stats.updated_metrics += 1
                continue
            if existing is None:
                _create_metric_from_candidate(session, oid, datasource_id, candidate, user_id)
                stats.created_metrics += 1
            else:
                _update_metric_candidate(session, existing, candidate, user_id)
                stats.updated_metrics += 1
            continue

        if dry_run:
            if existing is None:
                stats.created_dimensions += 1
            elif overwrite_candidate:
                stats.updated_dimensions += 1
            continue
        if existing is None:
            _create_dimension_from_candidate(session, oid, datasource_id, candidate, user_id)
            stats.created_dimensions += 1
        else:
            _update_dimension_candidate(session, existing, candidate, user_id)
            stats.updated_dimensions += 1

    record_semantic_metric(
        "semantic_candidate_init_total",
        phase="end",
        oid=oid,
        datasource_id=datasource_id,
        created_metrics=stats.created_metrics,
        updated_metrics=stats.updated_metrics,
        created_dimensions=stats.created_dimensions,
        updated_dimensions=stats.updated_dimensions,
        skipped=stats.skipped,
        dry_run=dry_run,
    )
    return stats
