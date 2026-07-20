from typing import Protocol

from apps.datasource.models.dto import (
    PhysicalField,
    PhysicalTable,
    PhysicalTableSnapshot,
)


class DatasourceMetadataRepository(Protocol):
    """物理表字段快照的仓储端口。"""

    def list_tables(self, datasource_id: int) -> list[PhysicalTable]: ...

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> list[PhysicalField]: ...

    def list_fields_by_table_ids(
        self,
        table_ids: list[int],
    ) -> dict[int, list[PhysicalField]]: ...

    def get_table(self, table_id: int) -> PhysicalTable | None: ...

    def replace_schema(
        self,
        datasource_id: int,
        snapshots: list[PhysicalTableSnapshot],
        total_table_count: int,
    ) -> None: ...

    def replace_fields(
        self,
        datasource_id: int,
        table_id: int,
        fields: list[PhysicalField],
    ) -> None: ...

    def update_table(self, table: PhysicalTable) -> None: ...

    def update_field(self, field: PhysicalField) -> None: ...

    def update_table_and_fields(
        self,
        table: PhysicalTable,
        fields: list[PhysicalField],
    ) -> None: ...
