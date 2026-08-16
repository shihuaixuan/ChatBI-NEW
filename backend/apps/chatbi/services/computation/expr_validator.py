"""ComputeTask.expr 的受限 SQL 表达式校验。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import sqlglot

from apps.chatbi.services.computation.errors import ComputeExpressionError

_ALLOWED_NODE_NAMES = frozenset(
    {
        "Abs",
        "Add",
        "And",
        "Avg",
        "Between",
        "Case",
        "Cast",
        "Coalesce",
        "Column",
        "Count",
        "DataType",
        "Div",
        "EQ",
        "GT",
        "GTE",
        "If",
        "Is",
        "IsNot",
        "Least",
        "Literal",
        "Max",
        "Min",
        "Mod",
        "Mul",
        "NEQ",
        "Not",
        "Nullif",
        "Or",
        "Paren",
        "Pow",
        "Round",
        "Sub",
        "Sum",
        "TryCast",
        "LT",
        "LTE",
        "Neg",
    }
)


def validate_expression(
    expression: str,
    allowed_columns: Iterable[str],
    *,
    allowed_qualifiers: Mapping[str, Iterable[str]] | None = None,
) -> str:
    """校验并规范化一个只能引用当前结果集字段的表达式。"""

    if not isinstance(expression, str) or not expression.strip():
        raise ComputeExpressionError("COMPUTE_EXPR_REQUIRED")
    if ";" in expression or "\x00" in expression:
        raise ComputeExpressionError("COMPUTE_EXPR_STATEMENT_NOT_ALLOWED")
    columns = {str(item) for item in allowed_columns}
    try:
        parsed = sqlglot.parse_one(expression, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise ComputeExpressionError("COMPUTE_EXPR_PARSE_FAILED") from exc
    for node in parsed.walk():
        node_name = type(node).__name__
        if node_name == "Column":
            column = node
            qualifier = str(getattr(column, "table", "") or "")
            if qualifier:
                qualifier_columns = (
                    set(allowed_qualifiers.get(qualifier, ()))
                    if allowed_qualifiers is not None
                    else set()
                )
                if not qualifier_columns:
                    raise ComputeExpressionError(
                        "COMPUTE_EXPR_TABLE_REFERENCE_FORBIDDEN"
                    )
                if column.name not in qualifier_columns:
                    raise ComputeExpressionError("COMPUTE_EXPR_COLUMN_UNKNOWN")
                continue
            if column.name not in columns:
                raise ComputeExpressionError("COMPUTE_EXPR_COLUMN_UNKNOWN")
            continue
        if node_name == "Identifier":
            continue
        if node_name == "Star":
            raise ComputeExpressionError("COMPUTE_EXPR_STAR_FORBIDDEN")
        if node_name not in _ALLOWED_NODE_NAMES:
            raise ComputeExpressionError(f"COMPUTE_EXPR_NODE_NOT_ALLOWED:{node_name}")
    return parsed.sql(dialect="duckdb")


__all__ = ["validate_expression"]
