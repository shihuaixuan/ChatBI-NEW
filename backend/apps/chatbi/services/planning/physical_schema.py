from collections.abc import Sequence
from typing import Protocol

from apps.chatbi.models import (
    PhysicalSchemaField,
    PhysicalSchemaResult,
    PhysicalSchemaTable,
)


class PhysicalTableView(Protocol):
    id: int | None
    checked: bool
    table_name: str
    table_comment: str | None
    custom_comment: str | None


class PhysicalFieldView(Protocol):
    checked: bool
    field_name: str
    field_type: str | None
    field_comment: str | None
    custom_comment: str | None


class DatasourceMetadataReader(Protocol):
    """ChatBI 读取数据源物理 Schema 的最小端口。"""

    def list_tables(
        self,
        datasource_id: int,
    ) -> Sequence[PhysicalTableView]: ...

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> Sequence[PhysicalFieldView]: ...


class PhysicalSchemaService:
    """统一读取 Agent 手写 SQL 所需的已启用物理表和字段。"""

    def __init__(self, metadata_reader: DatasourceMetadataReader) -> None:
        self._metadata_reader = metadata_reader

    def get(
        self,
        datasource_id: int,
        *,
        table_keyword: str = "",
    ) -> PhysicalSchemaResult:
        keyword = table_keyword.strip().lower()
        tables: list[PhysicalSchemaTable] = []
        for table in self._metadata_reader.list_tables(datasource_id):
            if not table.checked or table.id is None:
                continue
            comment = table.custom_comment or table.table_comment or ""
            if (
                keyword
                and keyword not in table.table_name.lower()
                and keyword not in comment.lower()
            ):
                continue
            fields = [
                PhysicalSchemaField(
                    name=field.field_name,
                    data_type=field.field_type,
                    comment=field.custom_comment or field.field_comment or "",
                )
                for field in self._metadata_reader.list_fields(table.id)
                if field.checked
            ]
            tables.append(
                PhysicalSchemaTable(
                    name=table.table_name,
                    comment=comment,
                    fields=fields,
                )
            )
        return PhysicalSchemaResult(tables=tables)


__all__ = ["DatasourceMetadataReader", "PhysicalSchemaService"]
