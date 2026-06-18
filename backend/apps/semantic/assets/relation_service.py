from __future__ import annotations

from typing import Any

from sqlalchemy import select

from apps.data_training.models.data_training_model import DataTraining
from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.enums import AssetStatus, AssetType, RelationType
from apps.semantic.models.asset_relation_model import AssetRelation
from apps.semantic.models.semantic_model import SemanticDimension, SemanticMetric
from apps.terminology.models.terminology_model import Terminology


class AssetRelationService:
    def __init__(self, session: Any | None = None):
        self.session = session

    def list_relations(
        self,
        asset_type: str,
        asset_id: int | str,
        scope: DatasetScope,
        asset: Any | None = None,
    ) -> list[AssetRelation]:
        relations = self._list_persisted_relations(asset_type, asset_id, scope)
        if relations:
            return relations
        if asset is None:
            asset = self._load_asset(asset_type, asset_id)
        return self.derive_relations(asset, scope) if asset is not None else []

    def upsert_relation(
        self,
        scope: DatasetScope,
        source_asset_type: str,
        source_asset_id: int | str,
        target_asset_type: str,
        target_asset_id: int | str,
        relation_type: str,
        weight: float = 1.0,
        description: str | None = None,
    ) -> AssetRelation:
        relation = AssetRelation(
            oid=scope.oid,
            datasource_id=scope.datasource_id,
            dataset_id=str(scope.dataset_id),
            source_asset_type=source_asset_type,
            source_asset_id=str(source_asset_id),
            target_asset_type=target_asset_type,
            target_asset_id=str(target_asset_id),
            relation_type=relation_type,
            weight=weight,
            description=description,
            status=AssetStatus.APPROVED.value,
        )
        if self.session is None or not hasattr(self.session, "exec"):
            return relation

        existing = self._find_relation(relation)
        if existing is not None:
            existing.weight = weight
            existing.description = description
            existing.status = AssetStatus.APPROVED.value
            self.session.add(existing)
            return existing
        self.session.add(relation)
        return relation

    def disable_relation(self, relation_id: int) -> bool:
        if self.session is None or not hasattr(self.session, "get"):
            return False
        relation = self.session.get(AssetRelation, relation_id)
        if relation is None:
            return False
        relation.status = AssetStatus.DISABLED.value
        self.session.add(relation)
        return True

    def derive_relations(self, asset: Any, scope: DatasetScope) -> list[AssetRelation]:
        if isinstance(asset, SemanticMetric):
            return self._derive_metric_relations(asset, scope)
        if isinstance(asset, SemanticDimension):
            return self._derive_dimension_relations(asset, scope)
        if isinstance(asset, Terminology):
            return self._derive_term_relations(asset, scope)
        if isinstance(asset, DataTraining):
            return self._derive_example_relations(asset, scope)
        return []

    def _derive_metric_relations(self, metric: SemanticMetric, scope: DatasetScope) -> list[AssetRelation]:
        relations: list[AssetRelation] = []
        if metric.field_id is not None:
            relations.append(
                self._runtime_relation(
                    scope,
                    AssetType.METRIC.value,
                    metric.id or metric.name,
                    AssetType.FIELD.value,
                    metric.field_id,
                    RelationType.USES_FIELD.value,
                )
            )
        for dimension_id in metric.related_dimension_ids or []:
            relations.append(
                self._runtime_relation(
                    scope,
                    AssetType.METRIC.value,
                    metric.id or metric.name,
                    AssetType.DIMENSION.value,
                    dimension_id,
                    RelationType.ANALYZABLE_BY.value,
                )
            )
        return relations

    def _derive_dimension_relations(self, dimension: SemanticDimension, scope: DatasetScope) -> list[AssetRelation]:
        if dimension.field_id is None:
            return []
        return [
            self._runtime_relation(
                scope,
                AssetType.DIMENSION.value,
                dimension.id or dimension.name,
                AssetType.FIELD.value,
                dimension.field_id,
                RelationType.USES_FIELD.value,
            )
        ]

    def _derive_term_relations(self, term: Terminology, scope: DatasetScope) -> list[AssetRelation]:
        relations = []
        for mapped_asset in term.mapped_assets or []:
            target_asset_type = mapped_asset.get("asset_type")
            target_asset_id = mapped_asset.get("asset_id")
            if not target_asset_type or target_asset_id is None:
                continue
            relations.append(
                self._runtime_relation(
                    scope,
                    AssetType.TERM.value,
                    term.id or term.word or "",
                    target_asset_type,
                    target_asset_id,
                    RelationType.MAPS_TO.value,
                )
            )
        return relations

    def _derive_example_relations(self, example: DataTraining, scope: DatasetScope) -> list[AssetRelation]:
        relations = []
        for linked_asset in example.linked_assets or []:
            target_asset_type = linked_asset.get("asset_type")
            target_asset_id = linked_asset.get("asset_id")
            if not target_asset_type or target_asset_id is None:
                continue
            relations.append(
                self._runtime_relation(
                    scope,
                    AssetType.EXAMPLE.value,
                    example.id or example.question or "",
                    target_asset_type,
                    target_asset_id,
                    RelationType.EXAMPLE_OF.value,
                )
            )
        return relations

    def _runtime_relation(
        self,
        scope: DatasetScope,
        source_asset_type: str,
        source_asset_id: int | str,
        target_asset_type: str,
        target_asset_id: int | str,
        relation_type: str,
        weight: float = 1.0,
    ) -> AssetRelation:
        return AssetRelation(
            oid=scope.oid,
            datasource_id=scope.datasource_id,
            dataset_id=str(scope.dataset_id),
            source_asset_type=source_asset_type,
            source_asset_id=str(source_asset_id),
            target_asset_type=target_asset_type,
            target_asset_id=str(target_asset_id),
            relation_type=relation_type,
            weight=weight,
            status=AssetStatus.APPROVED.value,
        )

    def _list_persisted_relations(self, asset_type: str, asset_id: int | str, scope: DatasetScope) -> list[AssetRelation]:
        if self.session is None or not hasattr(self.session, "exec"):
            return []
        result = self.session.exec(
            select(AssetRelation).where(
                AssetRelation.oid == scope.oid,
                AssetRelation.dataset_id == str(scope.dataset_id),
                AssetRelation.source_asset_type == asset_type,
                AssetRelation.source_asset_id == str(asset_id),
                AssetRelation.status == AssetStatus.APPROVED.value,
            )
        )
        return _all(result)

    def _find_relation(self, relation: AssetRelation) -> AssetRelation | None:
        result = self.session.exec(
            select(AssetRelation).where(
                AssetRelation.oid == relation.oid,
                AssetRelation.dataset_id == str(relation.dataset_id),
                AssetRelation.source_asset_type == relation.source_asset_type,
                AssetRelation.source_asset_id == str(relation.source_asset_id),
                AssetRelation.target_asset_type == relation.target_asset_type,
                AssetRelation.target_asset_id == str(relation.target_asset_id),
                AssetRelation.relation_type == relation.relation_type,
            )
        )
        return result.scalars().first() if hasattr(result, "scalars") else None

    def _load_asset(self, asset_type: str, asset_id: int | str) -> Any | None:
        if self.session is None or not hasattr(self.session, "get"):
            return None
        if asset_type == AssetType.METRIC.value:
            return self.session.get(SemanticMetric, asset_id)
        if asset_type == AssetType.DIMENSION.value:
            return self.session.get(SemanticDimension, asset_id)
        if asset_type == AssetType.TERM.value:
            return self.session.get(Terminology, asset_id)
        if asset_type == AssetType.EXAMPLE.value:
            return self.session.get(DataTraining, asset_id)
        return None


def _all(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "all"):
        return result.all()
    return list(result or [])
