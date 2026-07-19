import pytest

from apps.datasource.models.dto import (
    CreateDatasource,
    DatasourceRecord,
    PhysicalTable,
    UpdateDatasource,
)
from apps.datasource.services import DatasourceNameConflictError, DatasourceService


class RecordingDatasourceRepository:
    def __init__(self) -> None:
        self.records: dict[int, DatasourceRecord] = {}
        self.rollback_called = False
        self.events: list[str] = []
        self.conflicting_name: str | None = None

    def list_by_workspace(self, workspace_id: int) -> list[DatasourceRecord]:
        return [item for item in self.records.values() if item.oid == workspace_id]

    def get(self, datasource_id: int) -> DatasourceRecord | None:
        return self.records.get(datasource_id)

    def name_exists(
        self,
        workspace_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> bool:
        _ = workspace_id, exclude_id
        return name == self.conflicting_name

    def add_pending(self, datasource: DatasourceRecord) -> DatasourceRecord:
        created = datasource.model_copy(update={"id": 10})
        self.records[10] = created
        self.events.append("add")
        return created

    def update(self, datasource: DatasourceRecord) -> DatasourceRecord:
        assert datasource.id is not None
        self.records[datasource.id] = datasource
        self.events.append("update")
        return datasource

    def delete(self, datasource_id: int) -> None:
        self.events.append("delete")
        self.records.pop(datasource_id)

    def rollback(self) -> None:
        self.rollback_called = True
        self.records.clear()


class RecordingMetadataService:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[int, list[PhysicalTable]]] = []

    def sync_selected_tables(
        self,
        datasource_id: int,
        tables: list[PhysicalTable],
    ) -> None:
        self.calls.append((datasource_id, tables))
        if self.failure is not None:
            raise self.failure


class RecordingMaintenanceGateway:
    def __init__(self, *, cleanup_failure: Exception | None = None) -> None:
        self.cleanup_failure = cleanup_failure
        self.events: list[str] = []

    def get_type_name(self, datasource_type: str) -> str:
        return {"pg": "PostgreSQL", "mysql": "MySQL"}[datasource_type]

    def cleanup(self, datasource: DatasourceRecord) -> None:
        self.events.append(f"cleanup:{datasource.id}")
        if self.cleanup_failure is not None:
            raise self.cleanup_failure


def _service(
    repository: RecordingDatasourceRepository,
    metadata: RecordingMetadataService | None = None,
    gateway: RecordingMaintenanceGateway | None = None,
) -> DatasourceService:
    return DatasourceService(
        repository,
        metadata or RecordingMetadataService(),
        gateway or RecordingMaintenanceGateway(),
    )


def test_create_persists_datasource_and_metadata_in_one_application_flow():
    repository = RecordingDatasourceRepository()
    metadata = RecordingMetadataService()
    service = _service(repository, metadata)
    request = CreateDatasource(
        name="销售库",
        type="pg",
        configuration="encrypted",
        tables=[PhysicalTable(table_name="orders")],
    )

    result = service.create(request, actor_id=7, workspace_id=3)

    assert result.id == 10
    assert result.create_by == 7
    assert result.oid == 3
    assert result.type_name == "PostgreSQL"
    assert metadata.calls == [(10, request.tables)]
    assert not repository.rollback_called


def test_create_rolls_back_pending_datasource_when_metadata_sync_fails():
    repository = RecordingDatasourceRepository()
    metadata = RecordingMetadataService(failure=RuntimeError("字段读取失败"))
    service = _service(repository, metadata)

    with pytest.raises(RuntimeError, match="字段读取失败"):
        service.create(
            CreateDatasource(
                name="销售库",
                type="pg",
                configuration="encrypted",
            ),
            actor_id=7,
            workspace_id=3,
        )

    assert repository.rollback_called
    assert repository.records == {}


def test_duplicate_name_is_rejected_before_persistence():
    repository = RecordingDatasourceRepository()
    repository.conflicting_name = "销售库"
    service = _service(repository)

    with pytest.raises(DatasourceNameConflictError):
        service.create(
            CreateDatasource(
                name="销售库",
                type="pg",
                configuration="encrypted",
            ),
            actor_id=7,
            workspace_id=3,
        )

    assert repository.events == []


def test_update_preserves_server_fields_and_refreshes_type_name():
    repository = RecordingDatasourceRepository()
    repository.records[10] = DatasourceRecord(
        id=10,
        name="旧名称",
        description="旧描述",
        type="pg",
        type_name="PostgreSQL",
        configuration="old",
        create_by=7,
        oid=3,
        status="Success",
        table_relation=[{"id": "edge-1", "shape": "edge"}],
    )
    service = _service(repository)

    result = service.update(
        3,
        UpdateDatasource.model_validate(
            {
                "id": 10,
                "name": "新名称",
                "type": "mysql",
                "configuration": "new",
                "table_relation": [],
            }
        ),
    )

    assert result.name == "新名称"
    assert result.description == "旧描述"
    assert result.type_name == "MySQL"
    assert result.create_by == 7
    assert result.oid == 3
    assert result.table_relation == [{"id": "edge-1", "shape": "edge"}]


def test_external_cleanup_failure_prevents_local_datasource_delete():
    repository = RecordingDatasourceRepository()
    repository.records[10] = DatasourceRecord(
        id=10,
        name="Excel 数据源",
        type="excel",
        configuration="encrypted",
        oid=3,
    )
    gateway = RecordingMaintenanceGateway(
        cleanup_failure=RuntimeError("物理表清理失败")
    )
    service = _service(repository, gateway=gateway)

    with pytest.raises(RuntimeError, match="物理表清理失败"):
        service.delete(10)

    assert gateway.events == ["cleanup:10"]
    assert repository.events == []
    assert 10 in repository.records
