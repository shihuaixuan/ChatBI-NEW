from apps.semantic.models.dto import DatasetPayload
from apps.semantic.models.orm import SemanticDataset
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
        ),
    )

    assert result is dataset
    assert dataset.name == "经营分析数据集"
    assert dataset.schema_version == 4
    assert repository.updated == [dataset]


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
        lambda current_session, current_dataset: current_session.events.append(
            ("sync", current_dataset)
        ),
    )

    result = SqlModelDatasetRepository(session).create(dataset)

    assert result is dataset
    assert session.events == [
        ("add", dataset),
        ("flush", None),
        ("refresh", dataset),
        ("sync", dataset),
        ("commit", None),
        ("refresh", dataset),
    ]


class _DatasetRepository:
    def __init__(self, dataset: SemanticDataset):
        self.dataset = dataset
        self.updated: list[SemanticDataset] = []

    def get_active(self, oid: int, dataset_id: int):
        if self.dataset.oid == oid and self.dataset.id == dataset_id:
            return self.dataset
        return None

    def update(self, dataset: SemanticDataset):
        self.updated.append(dataset)
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
