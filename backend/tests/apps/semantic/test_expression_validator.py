import pytest
from sqlalchemy import delete
from sqlmodel import Session

from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.models.semantic_schema import ValidateExpressionRequest
from apps.semantic.services.expression_validator import (
    build_metric_check_sql,
    ensure_expr_fields_in_whitelist,
    load_field_whitelist,
    reject_unsafe_sql,
    validate_dimension_expr,
    validate_metric_expr,
)
from common.core.db import engine


@pytest.mark.parametrize(
    "fragment",
    [
        "pay_amount",
        "pay_amount * 0.9",
        "CASE WHEN status = 'paid' THEN pay_amount ELSE 0 END",
        "COALESCE(pay_amount, 0)",
    ],
)
def test_reject_unsafe_sql_allows_safe_expression_fragments(fragment: str):
    reject_unsafe_sql(fragment)


@pytest.mark.parametrize(
    "fragment",
    [
        "pay_amount; DROP TABLE orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "CREATE TABLE t(id int)",
        "INSERT INTO orders VALUES (1)",
        "UPDATE orders SET pay_amount = 0",
        "DELETE FROM orders",
        "TRUNCATE TABLE orders",
        "MERGE INTO orders",
        "pay_amount -- comment escape",
        "pay_amount /* comment */",
    ],
)
def test_reject_unsafe_sql_rejects_dangerous_fragments(fragment: str):
    with pytest.raises(ValueError, match="SEMANTIC_EXPR_INVALID"):
        reject_unsafe_sql(fragment)


@pytest.mark.parametrize("fragment", ["order_status = 'paid' ORDER BY id", "order_status = 'paid' LIMIT 10"])
def test_reject_unsafe_sql_rejects_filter_order_by_and_limit(fragment: str):
    with pytest.raises(ValueError, match="SEMANTIC_EXPR_INVALID"):
        reject_unsafe_sql(fragment, is_filter=True)


def cleanup_field_whitelist_datasource(session: Session, datasource_id: int):
    session.execute(delete(CoreField).where(CoreField.ds_id == datasource_id))
    session.execute(delete(CoreTable).where(CoreTable.ds_id == datasource_id))
    session.commit()


def create_whitelist_table(session: Session, datasource_id: int, checked: bool = True) -> CoreTable:
    table = CoreTable(
        ds_id=datasource_id,
        checked=checked,
        table_name="orders",
        table_comment="订单表",
        custom_comment="",
    )
    session.add(table)
    session.flush()
    session.refresh(table)
    for index, field_name in enumerate(["pay_amount", "status", "created_at"], start=1):
        session.add(
            CoreField(
                ds_id=datasource_id,
                table_id=table.id,
                checked=True,
                field_name=field_name,
                field_type="varchar",
                field_comment=field_name,
                custom_comment="",
                field_index=index,
            )
        )
    session.flush()
    return table


def test_load_field_whitelist_returns_checked_fields_for_table():
    datasource_id = 95100001

    with Session(engine) as session:
        cleanup_field_whitelist_datasource(session, datasource_id)
        table = create_whitelist_table(session, datasource_id)
        session.commit()

        whitelist = load_field_whitelist(session, datasource_id=datasource_id, table_id=table.id)

        assert whitelist == {"pay_amount", "status", "created_at"}

        cleanup_field_whitelist_datasource(session, datasource_id)


def test_load_field_whitelist_rejects_table_outside_datasource():
    datasource_id = 95100002

    with Session(engine) as session:
        cleanup_field_whitelist_datasource(session, datasource_id)
        cleanup_field_whitelist_datasource(session, datasource_id + 1)
        table = create_whitelist_table(session, datasource_id + 1)
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_SOURCE_FIELD_MISSING"):
            load_field_whitelist(session, datasource_id=datasource_id, table_id=table.id)

        cleanup_field_whitelist_datasource(session, datasource_id)
        cleanup_field_whitelist_datasource(session, datasource_id + 1)


def test_ensure_expr_fields_in_whitelist_rejects_unknown_fields():
    ensure_expr_fields_in_whitelist("CASE WHEN status = 'paid' THEN pay_amount ELSE 0 END", {"status", "pay_amount"})

    with pytest.raises(ValueError, match="SEMANTIC_SOURCE_FIELD_MISSING"):
        ensure_expr_fields_in_whitelist("unknown_amount + pay_amount", {"pay_amount"})


def test_build_metric_check_sql_wraps_aggregation_and_filter():
    request = ValidateExpressionRequest(
        table_id=1,
        expr="pay_amount",
        default_agg="SUM",
        filter_sql="status = 'paid'",
    )

    check_sql = build_metric_check_sql(request, table_name="orders")

    assert check_sql == "SELECT SUM(pay_amount) AS semantic_check_col FROM orders WHERE 1 = 0 AND (status = 'paid')"


def test_build_metric_check_sql_does_not_wrap_none_aggregation():
    request = ValidateExpressionRequest(table_id=1, expr="pay_amount * 0.9", default_agg="NONE")

    check_sql = build_metric_check_sql(request, table_name="orders")

    assert check_sql == "SELECT pay_amount * 0.9 AS semantic_check_col FROM orders WHERE 1 = 0"


def test_validate_metric_and_dimension_expr_return_valid_response():
    datasource_id = 95100003

    with Session(engine) as session:
        cleanup_field_whitelist_datasource(session, datasource_id)
        table = create_whitelist_table(session, datasource_id)
        session.commit()

        metric_response = validate_metric_expr(
            session,
            ValidateExpressionRequest(
                table_id=table.id,
                expr="pay_amount",
                default_agg="SUM",
                filter_sql="status = 'paid'",
            ),
        )
        dimension_response = validate_dimension_expr(
            session,
            ValidateExpressionRequest(table_id=table.id, expr="created_at", asset_type="DIMENSION"),
        )

        assert metric_response.valid is True
        assert metric_response.check_sql == "SELECT SUM(pay_amount) AS semantic_check_col FROM orders WHERE 1 = 0 AND (status = 'paid')"
        assert dimension_response.valid is True
        assert dimension_response.check_sql == "SELECT created_at AS semantic_check_col FROM orders WHERE 1 = 0"

        cleanup_field_whitelist_datasource(session, datasource_id)


def test_validate_metric_expr_returns_invalid_response_for_unknown_field_and_execution_failure():
    datasource_id = 95100004

    with Session(engine) as session:
        cleanup_field_whitelist_datasource(session, datasource_id)
        table = create_whitelist_table(session, datasource_id)
        session.commit()

        unknown_field_response = validate_metric_expr(
            session,
            ValidateExpressionRequest(table_id=table.id, expr="unknown_amount", default_agg="SUM"),
        )
        execution_failure_response = validate_metric_expr(
            session,
            ValidateExpressionRequest(table_id=table.id, expr="pay_amount", default_agg="SUM"),
            execute_check=lambda sql: (_ for _ in ()).throw(RuntimeError("database syntax error")),
        )

        assert unknown_field_response.valid is False
        assert "SEMANTIC_SOURCE_FIELD_MISSING" in unknown_field_response.error
        assert execution_failure_response.valid is False
        assert "database syntax error" in execution_failure_response.error

        cleanup_field_whitelist_datasource(session, datasource_id)
