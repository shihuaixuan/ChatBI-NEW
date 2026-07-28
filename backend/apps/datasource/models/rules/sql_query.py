"""Datasource 只读 SQL 校验规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
import sqlparse
from sqlglot import exp


@dataclass(frozen=True, slots=True)
class SQLRuleResult:
    sql: str | None
    tables: frozenset[str]
    error_code: str | None = None
    message: str = ""

    @property
    def valid(self) -> bool:
        return self.error_code is None


class ReadOnlySQLRule:
    """校验单语句、只读、表范围和查询上限。"""

    def __init__(self, default_limit: int | None = 100) -> None:
        self._default_limit = default_limit

    def validate(self, sql: str, allowed_tables: set[str]) -> SQLRuleResult:
        normalized = sql.strip()
        if not normalized:
            return self._invalid("empty_sql", "SQL 不能为空")
        statements = [item for item in sqlparse.split(normalized) if item.strip()]
        if len(statements) != 1:
            return self._invalid("multi_statement", "不允许执行多语句 SQL")

        statement = sqlparse.parse(statements[0])[0]
        first_token = statement.token_first(skip_cm=True)
        if not first_token or first_token.normalized.upper() not in {"SELECT", "WITH"}:
            return self._invalid("unsafe_statement", "只允许执行只读查询")
        if re.search(
            r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|call|execute)\b",
            statement.value,
            flags=re.IGNORECASE,
        ):
            return self._invalid("unsafe_statement", "SQL 包含不安全关键字")

        try:
            expression = sqlglot.parse_one(statement.value)
        except Exception:
            return self._invalid("sql_parse_failed", "SQL 无法解析")
        cte_names = {
            cte.alias_or_name.lower()
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }
        tables = {
            table.name
            for table in expression.find_all(exp.Table)
            if table.name and table.name.lower() not in cte_names
        }
        allowed = {table.lower() for table in allowed_tables}
        unknown = sorted(table for table in tables if table.lower() not in allowed)
        if unknown:
            return SQLRuleResult(
                sql=None,
                tables=frozenset(tables),
                error_code="table_out_of_scope",
                message=f"SQL 使用了授权范围外的表: {', '.join(unknown)}",
            )
        query = statement.value.strip().rstrip(";")
        if self._default_limit is not None and not re.search(
            r"\blimit\s+\d+\b",
            query,
            flags=re.IGNORECASE,
        ):
            query = f"{query} limit {self._default_limit}"
        return SQLRuleResult(sql=query, tables=frozenset(tables))

    @staticmethod
    def _invalid(error_code: str, message: str) -> SQLRuleResult:
        return SQLRuleResult(
            sql=None,
            tables=frozenset(),
            error_code=error_code,
            message=message,
        )

__all__ = ["ReadOnlySQLRule", "SQLRuleResult"]
