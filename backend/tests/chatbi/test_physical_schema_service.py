from apps.chatbi.services.planning import PhysicalSchemaService
from apps.datasource.models.dto import (
    DatasourceQueryPolicy,
    DatasourceQuerySubject,
    PhysicalField,
    PhysicalTable,
)


class StaticQueryService:
    def resolve_policy(self, subject, datasource_id):
        assert subject == DatasourceQuerySubject(user_id=7, workspace_id=3)
        assert datasource_id == 5
        return DatasourceQueryPolicy(
            authorized_tables=["orders"],
        )


class StaticMetadataReader:
    def list_tables(self, datasource_id: int) -> list[PhysicalTable]:
        assert datasource_id == 5
        return [
            PhysicalTable(
                id=10,
                ds_id=5,
                table_name="orders",
                table_comment="订单表",
            ),
            PhysicalTable(
                id=11,
                ds_id=5,
                table_name="users",
                table_comment="用户表",
            ),
            PhysicalTable(
                id=12,
                ds_id=5,
                table_name="disabled_table",
                checked=False,
            ),
        ]

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> list[PhysicalField]:
        assert table_id == 10
        assert keyword is None
        return [
            PhysicalField(
                table_id=10,
                field_name="amount",
                field_type="numeric",
                field_comment="金额",
            ),
            PhysicalField(
                table_id=10,
                field_name="internal_note",
                checked=False,
            ),
        ]


def test_physical_schema_service_filters_tables_and_fields_once():
    result = PhysicalSchemaService(
        StaticMetadataReader(),  # type: ignore[arg-type]
        StaticQueryService(),  # type: ignore[arg-type]
    ).get(
        5,
        subject=DatasourceQuerySubject(user_id=7, workspace_id=3),
        table_keyword="订单",
    )

    assert [table.name for table in result.tables] == ["orders"]
    assert [field.name for field in result.tables[0].fields] == ["amount"]
