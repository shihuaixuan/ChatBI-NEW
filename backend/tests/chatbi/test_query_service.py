"""Datasource 安全查询统一入口测试。"""

import time

import pytest

from apps.datasource.models.dto import (
    DatasourceDeniedColumn,
    DatasourceDriverResult,
    DatasourceQueryPolicy,
    DatasourceQueryRequest,
    DatasourceQueryRetryAdvice,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
    DatasourceRowFilter,
)
from apps.datasource.services import DatasourceQueryService


class StaticPolicyProvider:
    def __init__(self, policy: DatasourceQueryPolicy) -> None:
        self.policy = policy
        self.calls = []

    def resolve(self, subject, datasource_id):
        self.calls.append((subject, datasource_id))
        return self.policy


class RecordingExecutor:
    def __init__(self, result: DatasourceDriverResult | None = None) -> None:
        self.calls = []
        self.result = result or DatasourceDriverResult(
            succeeded=True,
            payload={
                "fields": ["amount"],
                "data": [{"amount": 10}, {"amount": 20}],
            },
        )

    def execute(self, datasource_id, sql):
        self.calls.append((datasource_id, sql))
        return self.result


class SequencedExecutor:
    def __init__(self, results: list[DatasourceDriverResult]) -> None:
        self.results = list(results)
        self.calls = []

    def execute(self, datasource_id, sql, *, timeout_seconds=None):
        self.calls.append((datasource_id, sql, timeout_seconds))
        return self.results.pop(0)


def _request(
    sql: str = "select amount from orders",
    *,
    selected_tables: list[str] | None = None,
) -> DatasourceQueryRequest:
    return DatasourceQueryRequest(
        sql=sql,
        datasource_id=8,
        subject=DatasourceQuerySubject(user_id=9, workspace_id=3),
        selected_tables=(
            ["orders"] if selected_tables is None else selected_tables
        ),
    )


def _service(
    *,
    authorized_tables: list[str] | None = None,
    row_filters: list[DatasourceRowFilter] | None = None,
    denied_columns: list[DatasourceDeniedColumn] | None = None,
    executor: RecordingExecutor | None = None,
) -> tuple[DatasourceQueryService, StaticPolicyProvider, RecordingExecutor]:
    provider = StaticPolicyProvider(
        DatasourceQueryPolicy(
            authorized_tables=(
                ["orders", "customers"]
                if authorized_tables is None
                else authorized_tables
            ),
            row_filters=row_filters or [],
            denied_columns=denied_columns or [],
        )
    )
    query_executor = executor or RecordingExecutor()
    return (
        DatasourceQueryService(provider, query_executor, sample_rows=1),
        provider,
        query_executor,
    )


def test_query_service_uses_authorized_and_selected_table_intersection():
    service, provider, executor = _service(
        row_filters=[
            DatasourceRowFilter(
                table="orders",
                condition="workspace_id = 3",
            )
        ]
    )

    result = service.execute(_request())

    assert result.status == DatasourceQueryStatus.SUCCEEDED
    assert provider.calls == [(_request().subject, 8)]
    assert executor.calls == [
        (
            8,
            "SELECT amount FROM orders WHERE orders.workspace_id = 3 LIMIT 100",
        )
    ]
    assert result.data is not None
    assert result.data.effective_tables == ["orders"]
    assert result.data.sample_rows == [{"amount": 10}]
    assert result.data.stats_summary["amount"]["sum"] == 30


@pytest.mark.parametrize(
    ("authorized_tables", "selected_tables", "error_code"),
    [
        ([], ["orders"], "authorized_tables_empty"),
        (["orders"], [], "selected_tables_required"),
        (["orders"], ["customers"], "effective_tables_empty"),
    ],
)
def test_query_service_never_treats_empty_scope_as_full_access(
    authorized_tables,
    selected_tables,
    error_code,
):
    service, _, executor = _service(authorized_tables=authorized_tables)

    result = service.execute(_request(selected_tables=selected_tables))

    assert result.status == DatasourceQueryStatus.REJECTED
    assert result.error_code == error_code
    assert executor.calls == []


def test_query_service_rejects_sql_table_outside_effective_scope():
    service, _, executor = _service()

    result = service.execute(
        _request("select name from customers", selected_tables=["orders"])
    )

    assert result.status == DatasourceQueryStatus.REJECTED
    assert result.error_code == "table_out_of_scope"
    assert executor.calls == []


@pytest.mark.parametrize(
    "sql",
    ["delete from orders", "select * from orders; select * from customers"],
)
def test_query_service_rejects_unsafe_or_multiple_statements(sql):
    service, _, executor = _service()

    result = service.execute(_request(sql))

    assert result.status == DatasourceQueryStatus.REJECTED
    assert executor.calls == []


def test_query_service_rejects_denied_column_before_execution():
    service, _, executor = _service(
        denied_columns=[
            DatasourceDeniedColumn(table="orders", column="secret_cost")
        ]
    )

    result = service.execute(_request("select secret_cost from orders"))

    assert result.status == DatasourceQueryStatus.REJECTED
    assert result.error_code == "column_permission_denied"
    assert executor.calls == []


def test_query_service_rejects_select_star_when_table_has_denied_column():
    service, _, executor = _service(
        denied_columns=[
            DatasourceDeniedColumn(table="orders", column="secret_cost")
        ]
    )

    result = service.execute(_request("select * from orders"))

    assert result.status == DatasourceQueryStatus.REJECTED
    assert result.error_code == "column_permission_denied"
    assert executor.calls == []


def test_query_service_treats_cte_name_as_query_alias_not_physical_table():
    service, _, executor = _service()

    result = service.execute(
        _request(
            "with recent_orders as (select amount from orders) "
            "select amount from recent_orders"
        )
    )

    assert result.status == DatasourceQueryStatus.SUCCEEDED
    assert executor.calls == [
        (
            8,
            "with recent_orders as (select amount from orders) "
            "select amount from recent_orders limit 100",
        )
    ]


def test_query_service_rejects_unauthorized_physical_table_inside_cte():
    service, _, executor = _service()

    result = service.execute(
        _request(
            "with private_orders as (select name from customers) "
            "select name from private_orders",
            selected_tables=["orders"],
        )
    )

    assert result.status == DatasourceQueryStatus.REJECTED
    assert result.error_code == "table_out_of_scope"
    assert executor.calls == []


def test_query_service_applies_row_filter_inside_cte_query_level():
    service, _, executor = _service(
        row_filters=[
            DatasourceRowFilter(
                table="orders",
                condition="workspace_id = 3",
            )
        ]
    )

    result = service.execute(
        _request(
            "with recent_orders as (select amount from orders) "
            "select amount from recent_orders"
        )
    )

    assert result.status == DatasourceQueryStatus.SUCCEEDED
    assert executor.calls == [
        (
            8,
            "WITH recent_orders AS (SELECT amount FROM orders "
            "WHERE orders.workspace_id = 3) "
            "SELECT amount FROM recent_orders LIMIT 100",
        )
    ]


def test_query_service_requires_policy_provider_and_executor():
    with pytest.raises(ValueError, match="POLICY_PROVIDER_REQUIRED"):
        DatasourceQueryService(None, RecordingExecutor())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="EXECUTOR_REQUIRED"):
        DatasourceQueryService(  # type: ignore[arg-type]
            StaticPolicyProvider(DatasourceQueryPolicy()),
            None,
        )


def test_query_service_classifies_transient_driver_failure_for_same_input_retry():
    executor = RecordingExecutor(
        DatasourceDriverResult(
            succeeded=False,
            error_code="connection_timeout",
            message="timeout",
            transient=True,
        )
    )
    service, _, _ = _service(executor=executor)

    result = service.execute(_request())

    assert result.status == DatasourceQueryStatus.FAILED
    assert result.retry_advice == DatasourceQueryRetryAdvice.SAME_INPUT


def test_query_service_classifies_sql_driver_failure_for_correct_input_retry():
    executor = RecordingExecutor(
        DatasourceDriverResult(
            succeeded=False,
            error_code="unknown_column",
            message="unknown column",
        )
    )
    service, _, _ = _service(executor=executor)

    result = service.execute(_request())

    assert result.status == DatasourceQueryStatus.FAILED
    assert result.retry_advice == DatasourceQueryRetryAdvice.CORRECT_INPUT


def test_query_service_retries_transient_failure_with_same_input_then_succeeds():
    executor = SequencedExecutor(
        [
            DatasourceDriverResult(
                succeeded=False,
                error_code="connection_timeout",
                message="timeout",
                transient=True,
            ),
            DatasourceDriverResult(
                succeeded=True,
                payload={"fields": ["amount"], "data": [{"amount": 10}]},
            ),
        ]
    )
    service, provider, _ = _service()
    service = DatasourceQueryService(
        provider,
        executor,
        sample_rows=1,
        max_transient_retries=1,
    )

    result = service.execute(_request())

    assert result.status == DatasourceQueryStatus.SUCCEEDED
    assert result.retry_count == 1
    assert len(executor.calls) == 2
    assert executor.calls[0][1] == executor.calls[1][1]


def test_query_service_stops_after_transient_retry_budget_is_exhausted():
    failure = DatasourceDriverResult(
        succeeded=False,
        error_code="connection_timeout",
        message="timeout",
        transient=True,
    )
    executor = SequencedExecutor([failure, failure])
    service, provider, _ = _service()
    service = DatasourceQueryService(
        provider,
        executor,
        max_transient_retries=1,
    )

    result = service.execute(_request())

    assert result.status == DatasourceQueryStatus.FAILED
    assert result.retry_count == 1
    assert result.retry_advice == DatasourceQueryRetryAdvice.SAME_INPUT
    assert len(executor.calls) == 2


def test_query_service_does_not_start_driver_after_deadline():
    executor = SequencedExecutor([])
    service, provider, _ = _service()
    service = DatasourceQueryService(provider, executor)
    request = _request().model_copy(
        update={"deadline_monotonic": time.monotonic() - 1}
    )

    result = service.execute(request)

    assert result.status == DatasourceQueryStatus.FAILED
    assert result.error_code == "query_deadline_exceeded"
    assert executor.calls == []
