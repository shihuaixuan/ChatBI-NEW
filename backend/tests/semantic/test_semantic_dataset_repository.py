import pytest

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import DatasetPayload
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
)
from apps.semantic.repository.dataset_repository import DatasetAssetReferenceFacts
from apps.semantic.repository.sqlmodel.dataset_repository import (
    SqlModelDatasetRepository,
)
from apps.semantic.services.dataset_service import (
    SemanticDatasetService,
)


def test_dataset_service_updates_schema_version_before_persisting():
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=2,
        name="经营数据集",
        biz_name="business",
        schema_version=3,
    )
    repository = _DatasetRepository(dataset)

    result = SemanticDatasetService(
        repository,
        _ActiveDomainRepository(),
    ).update_dataset(
        oid=1,
        dataset_id=20,
        payload=DatasetPayload(
            domain_id=2,
            name="经营分析数据集",
            biz_name="business_analysis",
                modelConfigs=[{"modelId": 9, "includesAll": True, "isDefault": True}],
        ),
    )

    assert result is dataset
    assert dataset.name == "经营分析数据集"
    assert dataset.schema_version == 4
    assert repository.updated == [dataset]


def test_dataset_service_passes_formal_assets_to_repository():
    dataset = SemanticDataset(
        id=21,
        oid=1,
        domain_id=2,
        name="经营数据集",
        biz_name="business",
    )
    repository = _DatasetRepository(dataset)

    SemanticDatasetService(
        repository,
        _ActiveDomainRepository(),
    ).update_dataset(
        oid=1,
        dataset_id=21,
        payload=DatasetPayload(
            domain_id=2,
            name="经营数据集",
            biz_name="business",
                modelConfigs=[{"modelId": 9, "includesAll": True, "isDefault": True}],
            assets=[{"assetType": "METRIC", "assetId": 100, "modelId": 9}],
        ),
    )

    assert repository.updated_model_configs[0].model_id == 9
    assert repository.updated_assets[0].asset_id == 100


def test_sqlmodel_dataset_repository_keeps_asset_sync_in_create_transaction(
    monkeypatch,
):
    session = _DatasetSession()
    dataset = SemanticDataset(
        oid=1,
        domain_id=2,
        name="经营数据集",
        biz_name="business",
    )

    monkeypatch.setattr(
        "apps.semantic.repository.sqlmodel.dataset_repository.sync_dataset_assets",
        lambda current_session, current_dataset, model_configs, assets: current_session.events.append(
            ("sync", current_dataset)
        ),
    )

    result = SqlModelDatasetRepository(session).create(dataset, [], [])

    assert result is dataset
    assert session.events == [
        ("add", dataset),
        ("flush", None),
        ("refresh", dataset),
        ("sync", dataset),
        ("commit", None),
        ("refresh", dataset),
    ]


def test_dataset_response_reads_formal_assets_without_legacy_detail():
    datasets = [
        SemanticDataset(
            id=30,
            oid=1,
            domain_id=2,
            name="经营数据集",
            biz_name="business",
            data_set_detail={},
        )
    ]
    repository = SqlModelDatasetRepository(
        _DatasetListSession(
            [
                datasets,
                [
                    SemanticDatasetModelConfig(
                        oid=1,
                        dataset_id=30,
                        model_id=9,
                        includes_all=True,
                        is_default=True,
                    )
                ],
                [
                    SemanticDatasetAsset(
                        oid=1,
                        dataset_id=30,
                        model_id=9,
                        asset_type="METRIC",
                        asset_id=100,
                    )
                ],
            ]
        )
    )

    result = repository.list_active_with_assets(1, 2)

    assert result[0].model_configs[0].model_id == 9
    assert result[0].assets[0].asset_id == 100
    serialized = result[0].model_dump(by_alias=True)
    assert serialized["model_configs"][0]["model_id"] == 9
    assert serialized["assets"][0]["asset_type"] == "METRIC"
    assert serialized["assets"][0]["asset_id"] == 100


def test_dataset_asset_validation_rejects_cross_domain_model():
    repository = _DatasetRepository(
        SemanticDataset(id=22, oid=1, domain_id=2, name="数据集", biz_name="dataset")
    )

    with pytest.raises(
        SemanticValidationError,
        match="SEMANTIC_DATASET_MODEL_REFERENCE_INVALID",
    ):
        SemanticDatasetService(repository, _ActiveDomainRepository()).update_dataset(
            1,
            22,
            DatasetPayload(
                domain_id=3,
                name="数据集",
                biz_name="dataset",
                modelConfigs=[{"modelId": 99, "isDefault": True}],
            ),
        )


class _DatasetRepository:
    def __init__(self, dataset: SemanticDataset):
        self.dataset = dataset
        self.updated: list[SemanticDataset] = []
        self.updated_model_configs = []
        self.updated_assets = []

    def get_active(self, oid: int, dataset_id: int):
        if self.dataset.oid == oid and self.dataset.id == dataset_id:
            return self.dataset
        return None

    def list_active_with_assets(self, oid: int, domain_id=None):
        return []

    def get_asset_reference_facts(
        self, oid, model_ids, metric_ids, dimension_ids, hierarchy_ids, relationship_ids
    ):
        return DatasetAssetReferenceFacts(
            models=dict.fromkeys(model_ids, (2, 1)),
            metrics=dict.fromkeys(metric_ids, (9, 1)),
            dimensions=dict.fromkeys(dimension_ids, (9, 1)),
            hierarchies=dict.fromkeys(hierarchy_ids, (2, 1)),
            relationships=dict.fromkeys(relationship_ids, (2, 1)),
        )

    def update(self, dataset: SemanticDataset, model_configs, assets):
        self.updated.append(dataset)
        self.updated_model_configs = model_configs
        self.updated_assets = assets
        return dataset


class _ActiveDomainRepository:
    def is_active(self, _oid: int, _domain_id: int) -> bool:
        return True


class _DatasetSession:
    def __init__(self):
        self.events: list[tuple[str, object | None]] = []

    def add(self, entity):
        self.events.append(("add", entity))

    def flush(self):
        self.events.append(("flush", None))

    def refresh(self, entity):
        self.events.append(("refresh", entity))

    def commit(self):
        self.events.append(("commit", None))


class _DatasetListSession:
    def __init__(self, results):
        self.results = list(results)

    def exec(self, _statement):
        return _Result(self.results.pop(0))


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values
