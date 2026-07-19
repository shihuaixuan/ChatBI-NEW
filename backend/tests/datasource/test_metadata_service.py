import pytest

from apps.datasource.models.dto import (
    ColumnSchema,
    PhysicalField,
    PhysicalTable,
    PhysicalTableSnapshot,
    TableSchema,
)
from apps.datasource.models.orm import CoreField, CoreTable
from apps.datasource.repository.sqlmodel.metadata_repository import (
    SQLModelDatasourceMetadataRepository,
)
from apps.datasource.services import (
    DatasourceMetadataService,
    DatasourceTableNotFoundError,
)


class RecordingMetadataRepository:
    def __init__(self) -> None:
        self.replaced_schema: (
            tuple[
                int,
                list[PhysicalTableSnapshot],
                int,
            ]
            | None
        ) = None

    def list_tables(self, datasource_id: int) -> list[PhysicalTable]:
        return []

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> list[PhysicalField]:
        return []

    def get_table(self, table_id: int) -> PhysicalTable | None:
        return None

    def replace_schema(
        self,
        datasource_id: int,
        snapshots: list[PhysicalTableSnapshot],
        total_table_count: int,
    ) -> None:
        self.replaced_schema = (datasource_id, snapshots, total_table_count)

    def replace_fields(
        self,
        datasource_id: int,
        table_id: int,
        fields: list[PhysicalField],
    ) -> None:
        raise AssertionError("本测试不应同步单表字段")

    def update_table(self, table: PhysicalTable) -> None:
        raise AssertionError("本测试不应修改表")

    def update_field(self, field: PhysicalField) -> None:
        raise AssertionError("本测试不应修改字段")

    def update_table_and_fields(
        self,
        table: PhysicalTable,
        fields: list[PhysicalField],
    ) -> None:
        raise AssertionError("本测试不应批量修改注释")


class FakeConnectionService:
    def __init__(self, *, fail_table: str | None = None) -> None:
        self._fail_table = fail_table

    def list_tables(self, datasource_id: int) -> list[TableSchema]:
        return [
            TableSchema("orders", "订单"),
            TableSchema("customers", "客户"),
            TableSchema("products", "商品"),
        ]

    def list_fields(
        self,
        datasource_id: int,
        table_name: str,
    ) -> list[ColumnSchema]:
        if table_name == self._fail_table:
            raise RuntimeError("字段读取失败")
        return [ColumnSchema("id", "bigint", "主键")]


def test_selected_tables_are_persisted_only_after_all_remote_fields_are_loaded():
    repository = RecordingMetadataRepository()
    service = DatasourceMetadataService(
        repository,
        FakeConnectionService(fail_table="customers"),
    )

    with pytest.raises(RuntimeError, match="字段读取失败"):
        service.sync_selected_tables(
            10,
            [
                PhysicalTable(table_name="orders"),
                PhysicalTable(table_name="customers"),
            ],
        )

    assert repository.replaced_schema is None


def test_selected_tables_replace_one_complete_snapshot_and_update_count():
    repository = RecordingMetadataRepository()
    service = DatasourceMetadataService(repository, FakeConnectionService())

    service.sync_selected_tables(
        10,
        [
            PhysicalTable(table_name="orders"),
            PhysicalTable(table_name="customers"),
        ],
    )

    assert repository.replaced_schema is not None
    datasource_id, snapshots, total_count = repository.replaced_schema
    assert datasource_id == 10
    assert total_count == 3
    assert [item.table_name for item in snapshots] == ["orders", "customers"]
    assert snapshots[0].fields[0].field_name == "id"


def test_unknown_selected_table_is_rejected_before_persistence():
    repository = RecordingMetadataRepository()
    service = DatasourceMetadataService(repository, FakeConnectionService())

    with pytest.raises(DatasourceTableNotFoundError):
        service.sync_selected_tables(
            10,
            [PhysicalTable(table_name="missing")],
        )

    assert repository.replaced_schema is None


class FailingCommitSession:
    def __init__(self) -> None:
        self.table = CoreTable(
            id=1,
            ds_id=10,
            checked=True,
            table_name="orders",
            table_comment="订单",
            custom_comment="订单",
        )
        self.field = CoreField(
            id=2,
            ds_id=10,
            table_id=1,
            checked=True,
            field_name="id",
            field_type="bigint",
            field_comment="主键",
            custom_comment="主键",
            field_index=0,
        )
        self.rollback_called = False

    def get(self, model, object_id):
        if model is CoreTable and object_id == 1:
            return self.table
        if model is CoreField and object_id == 2:
            return self.field
        return None

    def add(self, _row) -> None:
        return None

    def commit(self) -> None:
        raise RuntimeError("提交失败")

    def rollback(self) -> None:
        self.rollback_called = True


def test_comment_batch_update_rolls_back_when_commit_fails():
    session = FailingCommitSession()
    repository = SQLModelDatasourceMetadataRepository(session)

    with pytest.raises(RuntimeError, match="提交失败"):
        repository.update_table_and_fields(
            PhysicalTable(
                id=1,
                ds_id=10,
                table_name="orders",
                custom_comment="订单事实表",
            ),
            [
                PhysicalField(
                    id=2,
                    ds_id=10,
                    table_id=1,
                    field_name="id",
                    custom_comment="订单编号",
                )
            ],
        )

    assert session.rollback_called
