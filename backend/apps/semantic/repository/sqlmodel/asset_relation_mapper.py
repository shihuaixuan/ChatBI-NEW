from __future__ import annotations

from typing import Any

from apps.semantic.models.orm import (
    SemanticAssetAlias,
    SemanticAssetRelation,
    SemanticDatasetAsset,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticTerm,
)
from apps.semantic.utils.text import unique_texts


def build_aliases_for_asset(
    asset_type: str,
    asset_id: int | None,
    oid: int,
    aliases: list[str] | None,
    alias_type: str = "MANUAL",
) -> list[SemanticAssetAlias]:
    if asset_id is None:
        return []
    return [
        SemanticAssetAlias(
            oid=oid,
            asset_type=asset_type,
            asset_id=asset_id,
            alias=alias,
            alias_type=alias_type,
            priority=index,
        )
        for index, alias in enumerate(unique_texts(aliases or []))
    ]


def build_metric_relations(metric: SemanticMetric) -> list[SemanticAssetRelation]:
    relations: list[SemanticAssetRelation] = []
    if metric.id is None:
        return relations
    if metric.measure_id is not None:
        relations.append(_relation(metric.oid, "METRIC", metric.id, "DEFINED_BY_MEASURE", "MEASURE", metric.measure_id))
    if metric.field_id is not None:
        relations.append(_relation(metric.oid, "METRIC", metric.id, "USES_FIELD", "FIELD", metric.field_id))
    for metric_ref in metric.metric_refs or []:
        relations.append(_relation(metric.oid, "METRIC", metric.id, "DEFINED_BY_METRIC", "METRIC", metric_ref))
    for item in metric.relate_dimensions or []:
        dimension_id = _relation_asset_id(item)
        if dimension_id is not None:
            relations.append(_relation(metric.oid, "METRIC", metric.id, "ANALYZABLE_BY", "DIMENSION", dimension_id))
    return relations


def build_dimension_relations(dimension: SemanticDimension) -> list[SemanticAssetRelation]:
    if dimension.id is None or dimension.field_id is None:
        return []
    return [_relation(dimension.oid, "DIMENSION", dimension.id, "USES_FIELD", "FIELD", dimension.field_id)]


def build_dimension_value_relations(value: SemanticDimensionValue) -> list[SemanticAssetRelation]:
    if value.id is None:
        return []
    return [_relation(value.oid, "VALUE", value.id, "BELONGS_TO", "DIMENSION", value.dimension_id)]


def build_term_relations(term: SemanticTerm) -> list[SemanticAssetRelation]:
    if term.id is None:
        return []
    relations = [
        _relation(term.oid, "TERM", term.id, "RELATED_TO", "METRIC", metric_id)
        for metric_id in term.related_metrics or []
    ]
    relations.extend(
        _relation(term.oid, "TERM", term.id, "RELATED_TO", "DIMENSION", dimension_id)
        for dimension_id in term.related_dimensions or []
    )
    return relations


def build_dataset_asset_relations(assets: list[SemanticDatasetAsset]) -> list[SemanticAssetRelation]:
    relations: list[SemanticAssetRelation] = []
    for asset in assets:
        if asset.status != 1 or asset.id is None:
            continue
        relations.append(
            _relation(
                oid=asset.oid,
                source_type="DATASET",
                source_id=asset.dataset_id,
                relation_type="EXPOSED_BY_DATASET",
                target_type=asset.asset_type,
                target_id=asset.asset_id,
                metadata={"model_id": asset.model_id},
            )
        )
    return relations


def _relation(
    oid: int,
    source_type: str,
    source_id: int,
    relation_type: str,
    target_type: str,
    target_id: int,
    metadata: dict[str, Any] | None = None,
) -> SemanticAssetRelation:
    return SemanticAssetRelation(
        oid=oid,
        source_type=source_type,
        source_id=source_id,
        relation_type=relation_type,
        target_type=target_type,
        target_id=target_id,
        relation_metadata=metadata or {},
    )


def _relation_asset_id(item: dict[str, Any]) -> int | None:
    for key in ["id", "dimensionId", "dimension_id", "asset_id", "assetId"]:
        value = item.get(key)
        if isinstance(value, int):
            return value
    return None
