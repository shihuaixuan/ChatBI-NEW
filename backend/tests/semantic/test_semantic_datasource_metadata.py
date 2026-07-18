import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.api.datasources import list_datasource_columns
from apps.semantic.errors import SemanticForbiddenError
from apps.semantic.repository.datasource.metadata_discovery import (
    column_metas_from_persisted_fields,
    table_metas_from_persisted_tables,
)
from apps.semantic.services.datasource_service import (
    SemanticDatasourceService,
)


def test_table_metas_from_persisted_tables_returns_only_checked_tables():
    mojibake_comment = "æ¡£å\x8f£æµ\x81é‡\x8fä¸Žè½¬åŒ–æŒ‡æ\xa0‡è¡¨"
    tables = [
        CoreTable(
            id=1,
            ds_id=10,
            checked=True,
            table_name="traffic_daily",
            table_comment="流量日表",
            custom_comment="",
        ),
        CoreTable(
            id=2,
            ds_id=10,
            checked=False,
            table_name="orders",
            table_comment="订单表",
            custom_comment="订单明细",
        ),
        CoreTable(
            id=3,
            ds_id=10,
            checked=True,
            table_name="stall_traffic_metrics",
            table_comment="乱码注释",
            custom_comment=mojibake_comment,
        ),
    ]

    result = table_metas_from_persisted_tables(tables)

    assert [item.table_name for item in result] == [
        "traffic_daily",
        "stall_traffic_metrics",
    ]
    assert result[1].table_comment == "档口流量与转化指标表"


def test_column_metas_from_persisted_fields_returns_only_checked_fields():
    mojibake_comment = "è®¿é\x97®äººæ•°ï¼\x88UVï¼\x89"
    fields = [
        CoreField(
            id=1,
            ds_id=10,
            table_id=100,
            checked=True,
            field_name="visit_uv",
            field_type="bigint",
            field_comment="访问人数UV",
            custom_comment=mojibake_comment,
            field_index=1,
        ),
        CoreField(
            id=2,
            ds_id=10,
            table_id=100,
            checked=False,
            field_name="internal_flag",
            field_type="varchar",
            field_comment="内部标记",
            custom_comment="",
            field_index=2,
        ),
    ]

    result = column_metas_from_persisted_fields(fields)

    assert [item.field_name for item in result] == ["visit_uv"]
    assert result[0].field_comment == "访问人数（UV）"


def test_datasource_service_rejects_cross_tenant_datasource_access():
    service = SemanticDatasourceService(_MissingDatasourceRepository())

    with pytest.raises(SemanticForbiddenError) as exc_info:
        service.list_datasource_tables(oid=1, datasource_id=10)

    assert exc_info.value.detail == "SEMANTIC_DATASOURCE_NOT_FOUND"


@pytest.mark.anyio
async def test_list_datasource_columns_unwraps_sqlalchemy_row_to_table_entity():
    table = CoreTable(
        id=100, ds_id=10, checked=True, table_name="stall_traffic_metrics"
    )
    field = CoreField(
        id=1,
        ds_id=10,
        table_id=100,
        checked=True,
        field_name="visit_uv",
        field_type="bigint",
        field_comment="访问人数",
        field_index=1,
    )
    session = _FakeSession(table=table, fields=[field])

    result = await list_datasource_columns(
        session, SimpleNamespace(oid=1), 10, "stall_traffic_metrics"
    )

    assert [item.field_name for item in result] == ["visit_uv"]


@pytest.mark.anyio
async def test_list_datasource_columns_maps_live_discovery_failure(monkeypatch):
    db_module = ModuleType("apps.db.db")

    def failed_get_fields(_datasource, _table_name):
        raise RuntimeError("连接失败")

    db_module.get_fields = failed_get_fields
    monkeypatch.setitem(sys.modules, "apps.db.db", db_module)

    with pytest.raises(HTTPException) as exc_info:
        await list_datasource_columns(
            _MissingMetadataSession(),
            SimpleNamespace(oid=1),
            10,
            "missing_table",
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "SEMANTIC_COLUMN_DISCOVERY_FAILED: 连接失败"


class _FakeSession:
    def __init__(self, table: CoreTable, fields: list[CoreField]):
        self.table = table
        self.fields = fields
        self.exec_calls = 0

    def execute(self, _statement):
        return _ExistsResult()

    def get(self, _model, datasource_id):
        return SimpleNamespace(id=datasource_id, oid=1)

    def exec(self, _statement):
        self.exec_calls += 1
        if self.exec_calls == 1:
            return _RowBackedScalarResult(self.table)
        return _ListScalarResult(self.fields)


class _MissingMetadataSession:
    def execute(self, _statement):
        return _ExistsResult()

    def exec(self, _statement):
        return _ScalarFirstResult(None)

    def get(self, _model, datasource_id):
        return SimpleNamespace(id=datasource_id, oid=1)


class _MissingDatasourceRepository:
    def is_accessible(self, _oid, _datasource_id):
        return False


class _ExistsResult:
    def first(self):
        return (10,)


class _RowBackedScalarResult:
    def __init__(self, scalar):
        self.scalar = scalar

    def first(self):
        # 模拟 SQLAlchemy Row：直接取 table.checked 会抛出 AttributeError。
        return SimpleNamespace(CoreTable=self.scalar)

    def scalars(self):
        return _ScalarFirstResult(self.scalar)


class _ScalarFirstResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _ListScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values
