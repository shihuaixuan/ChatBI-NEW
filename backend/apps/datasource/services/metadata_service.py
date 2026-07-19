from apps.datasource.models.dto import (
    PhysicalField,
    PhysicalTable,
    PhysicalTableSnapshot,
)
from apps.datasource.repository.metadata_repository import (
    DatasourceMetadataRepository,
)
from apps.datasource.services.connection_service import DatasourceConnectionService


class DatasourceTableNotFoundError(ValueError):
    """数据源中不存在请求的物理表。"""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        super().__init__(f"Datasource table {table_name} not found")


class DatasourceMetadataService:
    """物理表选择、字段同步和本地注释维护的统一入口。"""

    def __init__(
        self,
        repository: DatasourceMetadataRepository,
        connection_service: DatasourceConnectionService,
    ) -> None:
        self._repository = repository
        self._connection_service = connection_service

    def list_tables(self, datasource_id: int) -> list[PhysicalTable]:
        return self._repository.list_tables(datasource_id)

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> list[PhysicalField]:
        return self._repository.list_fields(table_id, keyword)

    def sync_selected_tables(
        self,
        datasource_id: int,
        selected_tables: list[PhysicalTable],
    ) -> None:
        remote_tables = self._connection_service.list_tables(datasource_id)
        remote_by_name = {item.tableName: item for item in remote_tables}

        snapshots: list[PhysicalTableSnapshot] = []
        for selected in selected_tables:
            remote = remote_by_name.get(selected.table_name)
            if remote is None:
                raise DatasourceTableNotFoundError(selected.table_name)
            fields = self._connection_service.list_fields(
                datasource_id,
                selected.table_name,
            )
            snapshots.append(
                PhysicalTableSnapshot(
                    table_name=selected.table_name,
                    table_comment=remote.tableComment or "",
                    fields=[
                        PhysicalField(
                            ds_id=datasource_id,
                            field_name=field.fieldName,
                            field_type=field.fieldType,
                            field_comment=field.fieldComment or "",
                            field_index=index,
                        )
                        for index, field in enumerate(fields)
                    ],
                )
            )

        # 所有远端字段读取成功后，才允许一次性替换本地快照。
        self._repository.replace_schema(
            datasource_id,
            snapshots,
            len(remote_tables),
        )

    def sync_table_fields(self, table_id: int) -> None:
        table = self._repository.get_table(table_id)
        if table is None or table.ds_id is None:
            raise DatasourceTableNotFoundError(str(table_id))

        remote_names = {
            item.tableName for item in self._connection_service.list_tables(table.ds_id)
        }
        if table.table_name not in remote_names:
            raise DatasourceTableNotFoundError(table.table_name)

        remote_fields = self._connection_service.list_fields(
            table.ds_id,
            table.table_name,
        )
        fields = [
            PhysicalField(
                ds_id=table.ds_id,
                table_id=table.id,
                field_name=field.fieldName,
                field_type=field.fieldType,
                field_comment=field.fieldComment or "",
                field_index=index,
            )
            for index, field in enumerate(remote_fields)
        ]
        self._repository.replace_fields(table.ds_id, table_id, fields)

    def update_table(self, table: PhysicalTable) -> None:
        self._repository.update_table(table)

    def update_field(self, field: PhysicalField) -> None:
        self._repository.update_field(field)

    def update_table_and_fields(
        self,
        table: PhysicalTable,
        fields: list[PhysicalField],
    ) -> None:
        self._repository.update_table_and_fields(table, fields)
