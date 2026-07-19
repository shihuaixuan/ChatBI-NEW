from apps.datasource.models.dto.connection import DatasourceConnection
from common.error import ParseSQLResultError
from infrastructure import query_execution
from infrastructure.query_execution import (
    ConnectionSnapshotSQLExecutor,
    build_legacy_chat_query_service,
)


def _connection() -> DatasourceConnection:
    return DatasourceConnection(
        id=8,
        type="mysql",
        configuration="encrypted",
    )


def test_connection_snapshot_executor_preserves_driver_result(monkeypatch):
    monkeypatch.setattr(
        query_execution,
        "exec_sql",
        lambda connection, sql, origin_column: {
            "fields": ["amount"],
            "data": [{"amount": 10}],
            "sql": "encoded-sql",
        },
    )

    result = ConnectionSnapshotSQLExecutor(_connection()).run(
        {"datasource_id": 8, "sql": "select amount from orders"}
    )

    assert result.success
    assert result.payload == {
        "fields": ["amount"],
        "data": [{"amount": 10}],
        "sql": "encoded-sql",
    }


def test_connection_snapshot_executor_rejects_mismatched_datasource():
    result = ConnectionSnapshotSQLExecutor(_connection()).run(
        {"datasource_id": 9, "sql": "select 1"}
    )

    assert not result.success
    assert result.error_code == "datasource_not_found"


def test_connection_snapshot_executor_preserves_parse_error(monkeypatch):
    def raise_parse_error(*args, **kwargs):
        _ = args, kwargs
        raise ParseSQLResultError("结果转换失败")

    monkeypatch.setattr(query_execution, "exec_sql", raise_parse_error)

    result = ConnectionSnapshotSQLExecutor(_connection()).run(
        {"datasource_id": 8, "sql": "select 1"}
    )

    assert not result.success
    assert result.error_code == "sql_result_parse_error"
    assert result.message == "结果转换失败"


def test_connection_snapshot_executor_returns_clear_execution_error(monkeypatch):
    def raise_execute_error(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("连接中断")

    monkeypatch.setattr(query_execution, "exec_sql", raise_execute_error)

    result = ConnectionSnapshotSQLExecutor(_connection()).run(
        {"datasource_id": 8, "sql": "select 1"}
    )

    assert not result.success
    assert result.error_code == "sql_execute_error"
    assert result.message == "连接中断"


def test_legacy_chat_query_service_uses_limit_and_preserves_metadata(monkeypatch):
    executed_sql: list[str] = []

    def execute(_connection, sql, **_kwargs):
        executed_sql.append(sql)
        return {
            "fields": ["amount"],
            "data": [{"amount": 10}, {"amount": 20}],
            "sql": "encoded-sql",
        }

    monkeypatch.setattr(query_execution, "exec_sql", execute)
    service = build_legacy_chat_query_service(
        _connection(),
        enable_query_limit=True,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
        allowed_tables=["orders"],
    )

    assert result.success
    assert executed_sql == ["select amount from orders limit 1000"]
    assert result.payload["full_data"] == [{"amount": 10}, {"amount": 20}]
    assert result.payload["execution_metadata"] == {"sql": "encoded-sql"}


def test_legacy_chat_query_service_can_disable_automatic_limit(monkeypatch):
    executed_sql: list[str] = []

    def execute(_connection, sql, **_kwargs):
        executed_sql.append(sql)
        return {"fields": [], "data": [], "sql": "encoded-sql"}

    monkeypatch.setattr(query_execution, "exec_sql", execute)
    service = build_legacy_chat_query_service(
        _connection(),
        enable_query_limit=False,
    )

    result = service.execute_sql(
        sql="select 1",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
    )

    assert result.success
    assert executed_sql == ["select 1"]
