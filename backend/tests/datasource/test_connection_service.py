import json
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from apps.datasource.models.dto import DatasourceConnection
from apps.datasource.repository.connectors.connection_gateway import (
    DatabaseDriverConnectionGateway,
)
from apps.datasource.services import (
    DatasourceConnectionService,
    DatasourceNotFoundError,
)
from apps.datasource.utils.utils import aes_encrypt


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

    def check_connection(self, datasource: DatasourceConnection) -> bool:
        return True

    def get_version(self, datasource: DatasourceConnection) -> str:
        return "3.45"

    def get_tables(self, datasource: DatasourceConnection) -> list:
        return []

    def get_fields(self, datasource: DatasourceConnection, table_name: str) -> list:
        return []

    def execute_query(
        self,
        datasource: DatasourceConnection,
        sql: str,
        *,
        origin_column: bool = False,
    ) -> dict:
        self.executed = (datasource, sql, origin_column)
        return {"fields": ["value"], "data": [{"value": 1}], "sql": ""}


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
