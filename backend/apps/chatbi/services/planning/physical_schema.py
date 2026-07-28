from apps.chatbi.models import (
    PhysicalSchemaField,
    PhysicalSchemaResult,
    PhysicalSchemaTable,
)
from apps.datasource import DatasourceQuerySubject
from apps.datasource.services import DatasourceMetadataService, DatasourceQueryService


class PhysicalSchemaAccessDeniedError(PermissionError):
    """当前身份不能读取目标数据源的物理结构。"""


class PhysicalSchemaService:
    """统一读取 Agent 手写 SQL 所需的已启用物理表和字段（直连 Datasource 公开元数据服务）。"""

    def __init__(
        self,
        metadata_reader: DatasourceMetadataService,
        query_service: DatasourceQueryService,
    ) -> None:
        self._metadata_reader = metadata_reader
        self._query_service = query_service

    def get(
        self,
        datasource_id: int,
        *,
        subject: DatasourceQuerySubject,
        table_keyword: str = "",
    ) -> PhysicalSchemaResult:
        policy = self._query_service.resolve_policy(subject, datasource_id)
        if not policy.allowed or not policy.authorized_tables:
            raise PhysicalSchemaAccessDeniedError(
                policy.error_code or "physical_schema_access_denied"
            )
        authorized_tables = {
            table.lower() for table in policy.authorized_tables
        }
        denied_columns = {
            (item.table.lower(), item.column.lower())
            for item in policy.denied_columns
        }
        keyword = table_keyword.strip().lower()
        tables: list[PhysicalSchemaTable] = []
        for table in self._metadata_reader.list_tables(datasource_id):
            if not table.checked or table.id is None:
                continue
            if table.table_name.lower() not in authorized_tables:
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
                and (
                    table.table_name.lower(),
                    field.field_name.lower(),
                )
                not in denied_columns
            ]
            tables.append(
                PhysicalSchemaTable(
                    name=table.table_name,
                    comment=comment,
                    fields=fields,
                )
            )
        return PhysicalSchemaResult(tables=tables)


__all__ = ["PhysicalSchemaAccessDeniedError", "PhysicalSchemaService"]
