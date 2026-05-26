import re

import sqlparse
from sqlalchemy import select
from sqlparse import tokens as sql_tokens

from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.models.semantic_schema import (
    ValidateExpressionRequest,
    ValidateExpressionResponse,
)
from common.core.deps import SessionDep

DANGEROUS_KEYWORDS = {
    "ALTER",
    "CREATE",
    "DELETE",
    "DROP",
    "INSERT",
    "MERGE",
    "TRUNCATE",
    "UPDATE",
}
IGNORED_IDENTIFIERS = {
    "AND",
    "AS",
    "CASE",
    "CAST",
    "COALESCE",
    "ELSE",
    "END",
    "FALSE",
    "IS",
    "NULL",
    "OR",
    "THEN",
    "TRUE",
    "WHEN",
}


def _raise_invalid(reason: str) -> None:
    raise ValueError(f"SEMANTIC_EXPR_INVALID: {reason}")


def reject_unsafe_sql(fragment: str | None, is_filter: bool = False) -> None:
    if fragment is None or not fragment.strip():
        return

    sql = fragment.strip()
    upper_sql = sql.upper()

    if ";" in sql:
        _raise_invalid("multiple statements are not allowed")
    if "--" in sql or "/*" in sql or "*/" in sql:
        _raise_invalid("sql comments are not allowed")
    if is_filter and re.search(r"\b(ORDER\s+BY|LIMIT)\b", upper_sql):
        _raise_invalid("ORDER BY and LIMIT are not allowed in filter_sql")

    statements = [statement for statement in sqlparse.parse(sql) if str(statement).strip()]
    if len(statements) > 1:
        _raise_invalid("multiple statements are not allowed")

    tokens = {token.value.upper() for statement in statements for token in statement.flatten()}
    if tokens & DANGEROUS_KEYWORDS:
        _raise_invalid("DDL and DML keywords are not allowed")


def load_field_whitelist(session: SessionDep, datasource_id: int, table_id: int) -> set[str]:
    table = session.execute(
        select(CoreTable.id).where(
            CoreTable.id == table_id,
            CoreTable.ds_id == datasource_id,
            CoreTable.checked.is_(True),
        )
    ).first()
    if table is None:
        raise ValueError("SEMANTIC_SOURCE_FIELD_MISSING: table not found")

    field_names = set(
        session.execute(
            select(CoreField.field_name).where(
                CoreField.ds_id == datasource_id,
                CoreField.table_id == table_id,
                CoreField.checked.is_(True),
            )
        )
        .scalars()
        .all()
    )
    if not field_names:
        raise ValueError("SEMANTIC_SOURCE_FIELD_MISSING: fields not found")
    return field_names


def _extract_identifier_candidates(expr: str) -> set[str]:
    identifiers = set()
    for statement in sqlparse.parse(expr):
        flattened = list(statement.flatten())
        for index, token in enumerate(flattened):
            value = token.value
            if token.ttype in sql_tokens.Name:
                next_token = flattened[index + 1] if index + 1 < len(flattened) else None
                if next_token is not None and next_token.value == "(":
                    continue
                if value.upper() not in IGNORED_IDENTIFIERS:
                    identifiers.add(value)
    return identifiers


def ensure_expr_fields_in_whitelist(expr: str, field_names: set[str]) -> None:
    unknown_fields = _extract_identifier_candidates(expr) - field_names
    if unknown_fields:
        raise ValueError(f"SEMANTIC_SOURCE_FIELD_MISSING: {', '.join(sorted(unknown_fields))}")


def _value(source, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _load_table(session: SessionDep, table_id: int) -> CoreTable:
    table = session.execute(select(CoreTable).where(CoreTable.id == table_id, CoreTable.checked.is_(True))).scalar_one_or_none()
    if table is None:
        raise ValueError("SEMANTIC_SOURCE_FIELD_MISSING: table not found")
    return table


def _wrap_metric_expr(default_agg: str, expr: str) -> str:
    agg = (default_agg or "SUM").upper()
    if agg == "NONE":
        return expr
    if agg == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {expr})"
    return f"{agg}({expr})"


def build_metric_check_sql(metric_or_request, table_name: str | None = None) -> str:
    expr = _value(metric_or_request, "expr")
    default_agg = _value(metric_or_request, "default_agg", "SUM")
    filter_sql = _value(metric_or_request, "filter_sql")
    table = table_name or _value(metric_or_request, "table_name")
    if not table:
        raise ValueError("SEMANTIC_SOURCE_FIELD_MISSING: table_name not found")
    select_expr = _wrap_metric_expr(default_agg, expr)
    sql = f"SELECT {select_expr} AS semantic_check_col FROM {table} WHERE 1 = 0"
    if filter_sql:
        sql += f" AND ({filter_sql})"
    return sql


def build_dimension_check_sql(dimension_or_request, table_name: str | None = None) -> str:
    expr = _value(dimension_or_request, "expr")
    table = table_name or _value(dimension_or_request, "table_name")
    if not table:
        raise ValueError("SEMANTIC_SOURCE_FIELD_MISSING: table_name not found")
    return f"SELECT {expr} AS semantic_check_col FROM {table} WHERE 1 = 0"


def validate_metric_expr(
    session: SessionDep,
    request: ValidateExpressionRequest,
    execute_check=None,
) -> ValidateExpressionResponse:
    try:
        table = _load_table(session, request.table_id)
        reject_unsafe_sql(request.expr)
        reject_unsafe_sql(request.filter_sql, is_filter=True)
        field_names = load_field_whitelist(session, table.ds_id, request.table_id)
        ensure_expr_fields_in_whitelist(request.expr, field_names)
        if request.filter_sql:
            ensure_expr_fields_in_whitelist(request.filter_sql, field_names)
        check_sql = build_metric_check_sql(request, table_name=table.table_name)
        if execute_check is not None:
            execute_check(check_sql)
        return ValidateExpressionResponse(valid=True, check_sql=check_sql, warnings=[])
    except Exception as exc:
        return ValidateExpressionResponse(valid=False, check_sql="", warnings=[], error=str(exc))


def validate_dimension_expr(
    session: SessionDep,
    request: ValidateExpressionRequest,
    execute_check=None,
) -> ValidateExpressionResponse:
    try:
        table = _load_table(session, request.table_id)
        reject_unsafe_sql(request.expr)
        field_names = load_field_whitelist(session, table.ds_id, request.table_id)
        ensure_expr_fields_in_whitelist(request.expr, field_names)
        check_sql = build_dimension_check_sql(request, table_name=table.table_name)
        if execute_check is not None:
            execute_check(check_sql)
        return ValidateExpressionResponse(valid=True, check_sql=check_sql, warnings=[])
    except Exception as exc:
        return ValidateExpressionResponse(valid=False, check_sql="", warnings=[], error=str(exc))
