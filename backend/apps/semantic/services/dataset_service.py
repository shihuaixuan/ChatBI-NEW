from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import DatasetPayload, DatasetResponse
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.dataset_repository import DatasetRepository
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.utils.model_update import assign_values


class SemanticDatasetService:
    """数据集管理的应用服务。"""

    def __init__(
        self,
        repository: DatasetRepository,
        domain_reader: DomainRepository,
    ):
        self._repository = repository
        self._domain_reader = domain_reader

    def list_datasets(
        self, oid: int, domain_id: int | None = None
    ) -> list[DatasetResponse]:
        return self._repository.list_active_with_assets(oid, domain_id)

    def create_dataset(self, oid: int, payload: DatasetPayload) -> SemanticDataset:
        if not self._domain_reader.is_active(oid, payload.domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        self._validate_asset_references(oid, payload)
        values = payload.model_dump(exclude={"model_configs", "assets"})
        dataset = SemanticDataset(**values, oid=oid)
        return self._repository.create(dataset, payload.model_configs, payload.assets)

    def update_dataset(
        self, oid: int, dataset_id: int, payload: DatasetPayload
    ) -> SemanticDataset:
        dataset = self._repository.get_active(oid, dataset_id)
        if dataset is None:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
        if not self._domain_reader.is_active(oid, payload.domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        self._validate_asset_references(oid, payload)
        values = payload.model_dump(exclude={"model_configs", "assets"})
        assign_values(dataset, values)
        dataset.schema_version = (dataset.schema_version or 1) + 1
        # 数据集配置和业务日历属于发布契约，修改后必须重新发布。
        dataset.contract_version = 0
        return self._repository.update(dataset, payload.model_configs, payload.assets)

    def delete_dataset(self, oid: int, dataset_id: int) -> dict[str, int | bool]:
        dataset = self._repository.get_active(oid, dataset_id)
        if dataset is None:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
        self._repository.delete(dataset)
        return {"id": dataset_id, "deleted": True}

    def _validate_asset_references(self, oid: int, payload: DatasetPayload) -> None:
        """在正式资产替换前统一表达数据集配置和引用不变量。"""

        model_configs = payload.model_configs
        assets = payload.assets
        if not model_configs:
            raise SemanticValidationError("SEMANTIC_DATASET_MODEL_CONFIG_REQUIRED")
        model_ids = [item.model_id for item in model_configs]
        if len(model_ids) != len(set(model_ids)):
            raise SemanticValidationError("SEMANTIC_DATASET_MODEL_CONFIG_DUPLICATED")
        if sum(item.is_default for item in model_configs) != 1:
            raise SemanticValidationError("SEMANTIC_DATASET_DEFAULT_MODEL_REQUIRED")

        metric_ids = [item.asset_id for item in assets if item.asset_type == "METRIC"]
        dimension_ids = [
            item.asset_id for item in assets if item.asset_type == "DIMENSION"
        ]
        hierarchy_ids = [
            item.asset_id
            for item in assets
            if item.asset_type == "DIMENSION_HIERARCHY"
        ]
        relationship_ids = [
            item.asset_id
            for item in assets
            if item.asset_type == "METRIC_RELATIONSHIP"
        ]
        facts = self._repository.get_asset_reference_facts(
            oid,
            model_ids,
            metric_ids,
            dimension_ids,
            hierarchy_ids,
            relationship_ids,
        )
        if any(
            facts.models.get(model_id) != (payload.domain_id, 1)
            for model_id in model_ids
        ):
            raise SemanticValidationError("SEMANTIC_DATASET_MODEL_REFERENCE_INVALID")

        asset_keys = [(item.asset_type, item.asset_id) for item in assets]
        if len(asset_keys) != len(set(asset_keys)):
            raise SemanticValidationError("SEMANTIC_DATASET_ASSET_DUPLICATED")
        configured_model_ids = set(model_ids)
        for asset in assets:
            if asset.asset_type in {"METRIC", "DIMENSION"}:
                if asset.model_id is None or asset.model_id not in configured_model_ids:
                    raise SemanticValidationError(
                        "SEMANTIC_DATASET_ASSET_MODEL_REFERENCE_INVALID"
                    )
                source = (
                    facts.metrics.get(asset.asset_id)
                    if asset.asset_type == "METRIC"
                    else facts.dimensions.get(asset.asset_id)
                )
                if (
                    source is None
                    or source[0] != asset.model_id
                    or source[1] != 1
                ):
                    raise SemanticValidationError(
                        "SEMANTIC_DATASET_ASSET_REFERENCE_INVALID"
                    )
            elif asset.model_id is not None:
                raise SemanticValidationError(
                    "SEMANTIC_DATASET_ASSET_MODEL_REFERENCE_INVALID"
                )
        if set(metric_ids) != set(facts.metrics) or set(dimension_ids) != set(
            facts.dimensions
        ):
            raise SemanticValidationError("SEMANTIC_DATASET_ASSET_REFERENCE_INVALID")
        if any(
            facts.hierarchies.get(asset_id) != (payload.domain_id, 1)
            for asset_id in hierarchy_ids
        ):
            raise SemanticValidationError("SEMANTIC_DATASET_HIERARCHY_REFERENCE_INVALID")
        if any(
            facts.relationships.get(asset_id) != (payload.domain_id, 1)
            for asset_id in relationship_ids
        ):
            raise SemanticValidationError(
                "SEMANTIC_DATASET_RELATIONSHIP_REFERENCE_INVALID"
            )
