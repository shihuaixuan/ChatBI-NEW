from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlmodel import Session

from apps.semantic.models.orm import (
    SemanticAssetAlias,
    SemanticAssetRelation,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
    SemanticTerm,
)
from apps.semantic.repository.sqlmodel.asset_relation_mapper import (
    build_aliases_for_asset,
    build_dataset_asset_relations,
    build_dimension_relations,
    build_dimension_value_relations,
    build_metric_relations,
    build_term_relations,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.utils.orm_mapping import (
    dataset_assets_from_detail,
    dataset_model_configs_from_detail,
    dimension_values_from_maps,
    model_fields_from_detail,
    model_measures_from_detail,
)


def sync_model_structure(session: Session, model: SemanticModel) -> None:
    model.last_schema_sync_at = datetime.now()
    session.exec(
        delete(SemanticModelField).where(
            SemanticModelField.oid == model.oid, SemanticModelField.model_id == model.id
        )
    )
    session.exec(
        delete(SemanticModelMeasure).where(
            SemanticModelMeasure.oid == model.oid,
            SemanticModelMeasure.model_id == model.id,
        )
    )
    fields = model_fields_from_detail(model)
    for field in fields:
        field.model_id = model.id
        session.add(field)
    session.flush()
    for field in fields:
        if field.id is not None:
            aliases = build_aliases_for_asset("FIELD", field.id, field.oid, field.alias)
            _replace_aliases(session, field.oid, "FIELD", field.id, aliases)
    field_by_biz_name = {field.biz_name: field for field in fields}
    measures = model_measures_from_detail(model, field_by_biz_name)
    for measure in measures:
        measure.model_id = model.id
        session.add(measure)
    session.flush()
    for measure in measures:
        if measure.id is not None:
            aliases = build_aliases_for_asset(
                "MEASURE", measure.id, measure.oid, measure.alias
            )
            _replace_aliases(session, measure.oid, "MEASURE", measure.id, aliases)
    mark_model_schema_changed(session, model)


def mark_model_schema_changed(session: Session, model: SemanticModel) -> None:
    """统一递增模型及其业务域数据集的 Schema 版本。"""

    model.schema_version = (model.schema_version or 1) + 1
    session.add(model)
    mark_domain_datasets_schema_changed(session, model.oid, model.domain_id)


def mark_domain_datasets_schema_changed(
    session: Session, oid: int, domain_id: int
) -> None:
    datasets = all_results(
        session.exec(
            select(SemanticDataset).where(
                SemanticDataset.oid == oid,
                SemanticDataset.domain_id == domain_id,
                SemanticDataset.status == 1,
            )
        )
    )
    for dataset in datasets:
        dataset.schema_version = (dataset.schema_version or 1) + 1
        session.add(dataset)


def sync_dimension_values(session: Session, dimension: SemanticDimension) -> None:
    session.exec(
        delete(SemanticDimensionValue).where(
            SemanticDimensionValue.oid == dimension.oid,
            SemanticDimensionValue.dimension_id == dimension.id,
        )
    )
    for value in dimension_values_from_maps(dimension):
        value.dimension_id = dimension.id
        value.model_id = dimension.model_id
        session.add(value)
    session.flush()
    values = all_results(
        session.exec(
            select(SemanticDimensionValue).where(
                SemanticDimensionValue.oid == dimension.oid,
                SemanticDimensionValue.dimension_id == dimension.id,
                SemanticDimensionValue.status == 1,
            )
        )
    )
    for value in values:
        aliases = build_aliases_for_asset(
            "VALUE",
            value.id,
            value.oid,
            [value.display_value, *(value.alias or [])],
            alias_type="VALUE",
        )
        _replace_aliases(session, value.oid, "VALUE", value.id, aliases)
        _replace_relations(
            session,
            value.oid,
            "VALUE",
            value.id,
            build_dimension_value_relations(value),
        )


def sync_dataset_assets(session: Session, dataset: SemanticDataset) -> None:
    session.exec(
        delete(SemanticDatasetModelConfig).where(
            SemanticDatasetModelConfig.oid == dataset.oid,
            SemanticDatasetModelConfig.dataset_id == dataset.id,
        )
    )
    session.exec(
        delete(SemanticDatasetAsset).where(
            SemanticDatasetAsset.oid == dataset.oid,
            SemanticDatasetAsset.dataset_id == dataset.id,
        )
    )
    for config in dataset_model_configs_from_detail(dataset):
        config.dataset_id = dataset.id
        session.add(config)
    for asset in dataset_assets_from_detail(dataset):
        asset.dataset_id = dataset.id
        session.add(asset)
    session.flush()
    assets = all_results(
        session.exec(
            select(SemanticDatasetAsset).where(
                SemanticDatasetAsset.oid == dataset.oid,
                SemanticDatasetAsset.dataset_id == dataset.id,
                SemanticDatasetAsset.status == 1,
            )
        )
    )
    _replace_relations(
        session,
        dataset.oid,
        "DATASET",
        dataset.id,
        build_dataset_asset_relations(assets),
    )


def sync_metric_relations(session: Session, metric: SemanticMetric) -> None:
    if metric.id is None:
        return
    aliases = build_aliases_for_asset("METRIC", metric.id, metric.oid, metric.alias)
    _replace_aliases(session, metric.oid, "METRIC", metric.id, aliases)
    _replace_relations(
        session, metric.oid, "METRIC", metric.id, build_metric_relations(metric)
    )


def sync_dimension_relations(session: Session, dimension: SemanticDimension) -> None:
    if dimension.id is None:
        return
    aliases = build_aliases_for_asset(
        "DIMENSION", dimension.id, dimension.oid, dimension.alias
    )
    _replace_aliases(session, dimension.oid, "DIMENSION", dimension.id, aliases)
    _replace_relations(
        session,
        dimension.oid,
        "DIMENSION",
        dimension.id,
        build_dimension_relations(dimension),
    )


def sync_term_relations(session: Session, term: SemanticTerm) -> None:
    if term.id is None:
        return
    aliases = build_aliases_for_asset("TERM", term.id, term.oid, term.alias)
    _replace_aliases(session, term.oid, "TERM", term.id, aliases)
    _replace_relations(session, term.oid, "TERM", term.id, build_term_relations(term))


def _replace_aliases(
    session: Session,
    oid: int,
    asset_type: str,
    asset_id: int,
    aliases: list[SemanticAssetAlias],
) -> None:
    session.exec(
        delete(SemanticAssetAlias).where(
            SemanticAssetAlias.oid == oid,
            SemanticAssetAlias.asset_type == asset_type,
            SemanticAssetAlias.asset_id == asset_id,
        )
    )
    for alias in aliases:
        session.add(alias)


def _replace_relations(
    session: Session,
    oid: int,
    source_type: str,
    source_id: int,
    relations: list[SemanticAssetRelation],
) -> None:
    session.exec(
        delete(SemanticAssetRelation).where(
            SemanticAssetRelation.oid == oid,
            SemanticAssetRelation.source_type == source_type,
            SemanticAssetRelation.source_id == source_id,
        )
    )
    for relation in relations:
        session.add(relation)
