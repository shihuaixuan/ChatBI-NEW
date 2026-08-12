import json
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from apps.datasource.contracts import ExternalDatasource
from apps.datasource.external_connection import (
    build_external_datasource_connection,
)
from apps.datasource.models.dto import (
    ColumnSchema,
    DatasourceConf,
    DatasourceConnection,
    TableSchema,
)
from apps.datasource.repository.connectors import database as database_connector
from apps.datasource.repository.connectors.connection_gateway import (
    DatabaseDriverConnectionGateway,
)
from apps.datasource.repository.connectors.database import check_sql_read
from apps.datasource.repository.connectors.database_types import DB
from apps.datasource.repository.connectors.sql_templates import (
    get_field_sql,
    get_table_sql,
)
from apps.datasource.services import (
    DatasourceConnectionService,
    DatasourceNotFoundError,
)
from apps.datasource.utils.utils import aes_decrypt, aes_encrypt


class FakeDatasourceConnectionRepository:
    def __init__(self, datasource: DatasourceConnection | None) -> None:
        self._datasource = datasource

    def get_connection(self, datasource_id: int) -> DatasourceConnection | None:
        if self._datasource is None or self._datasource.id != datasource_id:
            return None
        return self._datasource


class RecordingConnectionGateway:
    def __init__(self) -> None:
        self.executed: tuple[DatasourceConnection, str, bool] | None = None
        self.calls: list[tuple[str, str]] = []
        self.timeout_seconds: float | None = None

    def check_connection(self, datasource: DatasourceConnection) -> bool:
        self.calls.append(("check", datasource.type))
        return True

    def get_version(self, datasource: DatasourceConnection) -> str:
        self.calls.append(("version", datasource.type))
        return "3.45"

    def get_tables(self, datasource: DatasourceConnection) -> list[TableSchema]:
        self.calls.append(("tables", datasource.type))
        return [TableSchema("orders", "订单")]

    def get_fields(
        self,
        datasource: DatasourceConnection,
        table_name: str,
    ) -> list[ColumnSchema]:
        self.calls.append((f"fields:{table_name}", datasource.type))
        return [ColumnSchema("id", "bigint", "主键")]

    def get_database_name(self, datasource: DatasourceConnection) -> str:
        self.calls.append(("database_name", datasource.type))
        return "analytics"

    def sample_rows(
        self,
        datasource: DatasourceConnection,
        table_name: str,
        field_names: list[str],
        *,
        limit: int,
    ) -> list[dict]:
        self.calls.append((f"sample:{table_name}", datasource.type))
        assert field_names == ["id"]
        assert limit == 3
        return [{"id": 1}]

    def execute_query(
        self,
        datasource: DatasourceConnection,
        sql: str,
        *,
        origin_column: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.calls.append(("query", datasource.type))
        self.executed = (datasource, sql, origin_column)
        self.timeout_seconds = timeout_seconds
        return {"fields": ["value"], "data": [{"value": 1}], "sql": ""}


def _external_connection(database: DB) -> DatasourceConnection:
    return build_external_datasource_connection(
        ExternalDatasource(
            id=9,
            name=f"{database.db_name} 数据源",
            type=database.type,
            host="db.example.com",
            port=5432,
            dataBase="analytics",
            user="sqlbot",
            password="secret",
            db_schema="public",
        ),
        timeout=12,
    )


def test_connection_service_uses_single_repository_and_gateway_boundary():
    datasource = DatasourceConnection(
        id=7,
        type="sqlite",
        type_name="SQLite",
        configuration="encrypted",
    )
    gateway = RecordingConnectionGateway()
    service = DatasourceConnectionService(
        FakeDatasourceConnectionRepository(datasource),
        gateway,
    )

    result = service.execute_query(7, "select 1", origin_column=True)

    assert result["data"] == [{"value": 1}]
    assert gateway.executed == (datasource, "select 1", True)


def test_connection_service_rejects_missing_datasource_before_driver_call():
    service = DatasourceConnectionService(
        FakeDatasourceConnectionRepository(None),
        RecordingConnectionGateway(),
    )

    with pytest.raises(DatasourceNotFoundError):
        service.list_tables(404)


def test_connection_service_forwards_query_deadline_to_driver_gateway():
    datasource = DatasourceConnection(
        id=7,
        type="mysql",
        type_name="MySQL",
        configuration="encrypted",
    )
    gateway = RecordingConnectionGateway()
    service = DatasourceConnectionService(
        FakeDatasourceConnectionRepository(datasource),
        gateway,
    )

    service.execute_query(7, "select 1", timeout_seconds=7.25)

    assert gateway.timeout_seconds == 7.25


def test_mysql_engine_applies_deadline_to_connect_read_and_write(
    monkeypatch,
):
    configuration = aes_encrypt(
        json.dumps(
            {
                "host": "db.example.com",
                "port": 3306,
                "username": "sqlbot",
                "password": "secret",
                "database": "analytics",
                "timeout": 30,
            }
        )
    )
    datasource = DatasourceConnection(
        id=7,
        type="mysql",
        type_name="MySQL",
        configuration=configuration,
    )
    captured: dict = {}

    def fake_create_engine(uri, **kwargs):
        captured["uri"] = uri
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(database_connector, "create_engine", fake_create_engine)

    database_connector.get_engine(datasource, timeout=7)

    assert captured["connect_args"] == {
        "connect_timeout": 7,
        "read_timeout": 7,
        "write_timeout": 7,
        "ssl": None,
    }


@pytest.mark.parametrize(
    ("datasource_type", "expected_sql"),
    [
        ("pg", 'SELECT "id" FROM "orders" LIMIT 3'),
        ("mysql", "SELECT `id` FROM `orders` LIMIT 3"),
        ("sqlServer", "SELECT TOP 3 [id] FROM [orders]"),
        ("oracle", 'SELECT "id" FROM "orders" WHERE ROWNUM <= 3'),
        ("hive", "SELECT `id` FROM orders LIMIT 3"),
    ],
)
def test_driver_gateway_builds_bounded_sample_query(
    monkeypatch,
    datasource_type: str,
    expected_sql: str,
):
    datasource = DatasourceConnection(
        id=7,
        type=datasource_type,
        configuration="encrypted",
    )
    gateway = DatabaseDriverConnectionGateway()
    calls: list[tuple[str, bool]] = []

    def fake_execute_query(
        _datasource,
        sql: str,
        *,
        origin_column: bool = False,
    ) -> dict:
        calls.append((sql, origin_column))
        return {"data": [{"id": 1}]}

    monkeypatch.setattr(gateway, "execute_query", fake_execute_query)

    assert gateway.sample_rows(
        datasource,
        "orders",
        ["id"],
        limit=3,
    ) == [{"id": 1}]
    assert calls == [(expected_sql, True)]


@pytest.mark.parametrize("database", list(DB))
def test_every_database_type_uses_the_same_connection_service_contract(database):
    connection = _external_connection(database)
    gateway = RecordingConnectionGateway()
    service = DatasourceConnectionService(
        FakeDatasourceConnectionRepository(connection),
        gateway,
    )

    assert service.check_connection(9)
    assert service.get_version(9) == "3.45"
    assert service.list_tables(9)[0].tableName == "orders"
    assert service.list_fields(9, "orders")[0].fieldName == "id"
    assert service.execute_query(9, "select 1")["data"] == [{"value": 1}]
    assert service.get_database_name(9) == "analytics"
    assert service.sample_rows(9, "orders", ["id"]) == [{"id": 1}]
    assert connection.type_name == database.db_name
    assert [operation for operation, _ in gateway.calls] == [
        "check",
        "version",
        "tables",
        "fields:orders",
        "query",
        "database_name",
        "sample:orders",
    ]


@pytest.mark.parametrize("database", list(DB))
def test_every_database_type_has_metadata_and_read_query_contract(database):
    connection = _external_connection(database)
    configuration = DatasourceConf(
        host="db.example.com",
        port=5432,
        username="sqlbot",
        password="secret",
        database="analytics",
        dbSchema="public",
        filename="/tmp/sqlbot.db",
    )

    table_sql, _table_parameter = get_table_sql(
        connection,
        configuration,
        "23.1",
    )
    field_sql, _field_parameter_1, _field_parameter_2 = get_field_sql(
        connection,
        configuration,
        "orders",
    )

    if database is DB.es:
        assert table_sql == ""
        assert field_sql == ""
    else:
        assert table_sql.strip()
        assert field_sql.strip()
    assert check_sql_read("select * from orders", connection)
    assert not check_sql_read("delete from orders", connection)


def test_external_connection_adapter_does_not_mutate_source_configuration():
    datasource = ExternalDatasource(
        id=9,
        name="销售库",
        type="pg",
        host="db.example.com",
        port=5432,
        dataBase="analytics",
        user="sqlbot",
        password="secret",
        db_schema="reporting",
    )

    connection = build_external_datasource_connection(datasource, timeout=17)
    configuration = json.loads(aes_decrypt(connection.configuration))

    assert datasource.configuration is None
    assert configuration["database"] == "analytics"
    assert configuration["dbSchema"] == "reporting"
    assert configuration["timeout"] == 17


def test_external_connection_adapter_rejects_unknown_database_type():
    with pytest.raises(ValueError, match="Unsupported datasource type"):
        build_external_datasource_connection(
            ExternalDatasource(name="未知库", type="unknown")
        )


def test_sqlite_connector_supports_detection_metadata_and_query(tmp_path):
    database_path = tmp_path / "datasource.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("create table sales (id integer, amount numeric)")
        connection.execute("insert into sales values (1, 12.5)")

    configuration = aes_encrypt(
        json.dumps(
            {
                "filename": str(database_path),
                "timeout": 10,
            }
        )
    )
    datasource = DatasourceConnection(
        id=1,
        type="sqlite",
        type_name="SQLite",
        configuration=configuration,
    )
    gateway = DatabaseDriverConnectionGateway()

    assert gateway.check_connection(datasource)
    assert [item.tableName for item in gateway.get_tables(datasource)] == ["sales"]
    assert [item.fieldName for item in gateway.get_fields(datasource, "sales")] == [
        "id",
        "amount",
    ]
    result = gateway.execute_query(datasource, "select id, amount from sales")
    assert result["fields"] == ["id", "amount"]
    assert result["data"] == [{"id": 1, "amount": 12.5}]


def test_sqlite_metadata_driver_error_is_not_converted_to_empty_list(tmp_path):
    missing_path = tmp_path / "missing" / "datasource.db"
    configuration = aes_encrypt(
        json.dumps(
            {
                "filename": str(missing_path),
                "timeout": 10,
            }
        )
    )
    datasource = DatasourceConnection(
        id=2,
        type="sqlite",
        configuration=configuration,
    )

    with pytest.raises(OperationalError):
        DatabaseDriverConnectionGateway().get_tables(datasource)
