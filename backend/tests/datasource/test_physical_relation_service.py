import pytest

from apps.datasource.models.dto import (
    PhysicalRelationCell,
    PhysicalRelationResources,
)
from apps.datasource.models.orm import CoreDatasource
from apps.datasource.models.rules.physical_relation import (
    retain_valid_physical_relation_cells,
)
from apps.datasource.repository.sqlmodel import (
    SQLModelDatasourcePhysicalRelationRepository,
)
from apps.datasource.services import (
    DatasourceNotFoundError,
    DatasourcePhysicalRelationError,
    DatasourcePhysicalRelationService,
)


class RecordingPhysicalRelationRepository:
    def __init__(self, resources: PhysicalRelationResources | None) -> None:
        self.resources = resources
        self.saved: tuple[int, list[PhysicalRelationCell]] | None = None

    def get_resources(
        self,
        datasource_id: int,
    ) -> PhysicalRelationResources | None:
        _ = datasource_id
        return self.resources

    def save(
        self,
        datasource_id: int,
        cells: list[PhysicalRelationCell],
    ) -> None:
        self.saved = (datasource_id, cells)


def _resources() -> PhysicalRelationResources:
    return PhysicalRelationResources(
        table_ids={1, 2},
        field_table_ids={11: 1, 12: 1, 21: 2, 22: 2},
    )


def _valid_cells() -> list[PhysicalRelationCell]:
    return [
        PhysicalRelationCell(
            id=1,
            shape="er-rect",
            position={"x": 10, "y": 20},
        ),
        PhysicalRelationCell(id=2, shape="er-rect"),
        PhysicalRelationCell(
            id="edge-1",
            shape="edge",
            source={"cell": 1, "port": 11},
            target={"cell": 2, "port": 22},
        ),
    ]


def test_valid_physical_relation_is_saved_with_graph_properties():
    repository = RecordingPhysicalRelationRepository(_resources())
    service = DatasourcePhysicalRelationService(repository)
    cells = _valid_cells()

    service.save_relations(8, cells)

    assert repository.saved == (8, cells)
    assert cells[0].model_dump()["position"] == {"x": 10, "y": 20}


def test_relation_rejects_table_outside_current_datasource():
    repository = RecordingPhysicalRelationRepository(_resources())
    service = DatasourcePhysicalRelationService(repository)

    with pytest.raises(DatasourcePhysicalRelationError, match="不属于数据源"):
        service.save_relations(
            8,
            [PhysicalRelationCell(id=3, shape="er-rect")],
        )

    assert repository.saved is None


def test_relation_rejects_field_that_does_not_belong_to_endpoint_table():
    repository = RecordingPhysicalRelationRepository(_resources())
    service = DatasourcePhysicalRelationService(repository)
    cells = _valid_cells()
    cells[-1] = PhysicalRelationCell(
        id="edge-1",
        shape="edge",
        source={"cell": 1, "port": 21},
        target={"cell": 2, "port": 22},
    )

    with pytest.raises(DatasourcePhysicalRelationError, match="来源字段"):
        service.save_relations(8, cells)

    assert repository.saved is None


def test_relation_rejects_self_reference():
    repository = RecordingPhysicalRelationRepository(_resources())
    service = DatasourcePhysicalRelationService(repository)

    with pytest.raises(DatasourcePhysicalRelationError, match="自身"):
        service.save_relations(
            8,
            [
                PhysicalRelationCell(
                    id="edge-1",
                    shape="edge",
                    source={"cell": 1, "port": 11},
                    target={"cell": 1, "port": 12},
                )
            ],
        )


def test_missing_datasource_is_reported_explicitly():
    service = DatasourcePhysicalRelationService(
        RecordingPhysicalRelationRepository(None)
    )

    with pytest.raises(DatasourceNotFoundError):
        service.list_relations(8)


def test_metadata_change_removes_nodes_and_edges_with_stale_endpoints():
    cells = _valid_cells()

    retained = retain_valid_physical_relation_cells(
        cells,
        table_ids={1},
        field_table_ids={11: 1, 12: 1},
    )

    assert [cell.id for cell in retained] == [1]


class FailingCommitSession:
    def __init__(self) -> None:
        self.datasource = CoreDatasource(
            id=8,
            name="销售库",
            type="pg",
            type_name="PostgreSQL",
            configuration="encrypted",
            create_by=7,
            oid=3,
            recommended_config=1,
        )
        self.rollback_called = False

    def get(self, model, object_id):
        if model is CoreDatasource and object_id == 8:
            return self.datasource
        return None

    def add(self, _row) -> None:
        return None

    def commit(self) -> None:
        raise RuntimeError("提交失败")

    def rollback(self) -> None:
        self.rollback_called = True


def test_repository_rolls_back_when_relation_commit_fails():
    session = FailingCommitSession()
    repository = SQLModelDatasourcePhysicalRelationRepository(session)

    with pytest.raises(RuntimeError, match="提交失败"):
        repository.save(8, _valid_cells())

    assert session.rollback_called
