import re

import sqlparse
from sqlparse.sql import Identifier, IdentifierList
from sqlparse.tokens import Keyword

from apps.agentic_chat.schemas import ToolResult


class SqlValidateTool:
    name = "sql.validate"

    def __init__(self, default_limit: int = 100):
        self.default_limit = default_limit

    def run(self, payload: dict) -> ToolResult:
        sql = (payload.get("sql") or "").strip()
        allowed_tables = {str(table).lower() for table in payload.get("allowed_tables") or []}
        if not sql:
            return ToolResult(success=False, error_code="empty_sql", message="SQL 不能为空")

        statements = [item for item in sqlparse.split(sql) if item.strip()]
        if len(statements) != 1:
            return ToolResult(success=False, error_code="multi_statement", message="不允许执行多语句 SQL")

        statement = sqlparse.parse(statements[0])[0]
        first_token = statement.token_first(skip_cm=True)
        if not first_token or first_token.normalized.upper() not in {"SELECT", "WITH"}:
            return ToolResult(success=False, error_code="unsafe_statement", message="只允许执行只读查询")

        if self._has_unsafe_keyword(statement.value):
            return ToolResult(success=False, error_code="unsafe_statement", message="SQL 包含不安全关键字")

        tables = self._extract_tables(statement)
        if allowed_tables and tables:
            unknown = sorted(table for table in tables if table.lower() not in allowed_tables)
            if unknown:
                return ToolResult(
                    success=False,
                    error_code="unknown_table",
                    message=f"SQL 使用了未检索到的表: {', '.join(unknown)}",
                )

        normalized_sql = self._ensure_limit(statement.value.strip().rstrip(";"))
        return ToolResult(success=True, payload={"sql": normalized_sql, "tables": sorted(tables)})

    @staticmethod
    def _has_unsafe_keyword(sql: str) -> bool:
        pattern = r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|call|execute)\b"
        return re.search(pattern, sql, flags=re.IGNORECASE) is not None

    def _extract_tables(self, statement) -> set[str]:
        tables: set[str] = set()
        expect_table = False
        for token in statement.tokens:
            if token.is_group and not isinstance(token, Identifier | IdentifierList):
                tables.update(self._extract_tables(token))
            if token.ttype is Keyword and token.normalized.upper() in {"FROM", "JOIN", "UPDATE", "INTO"}:
                expect_table = True
                continue
            if expect_table:
                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        self._add_identifier_table(tables, identifier)
                    expect_table = False
                elif isinstance(token, Identifier):
                    self._add_identifier_table(tables, token)
                    expect_table = False
                elif token.ttype is Keyword:
                    expect_table = False
        return tables

    @staticmethod
    def _add_identifier_table(tables: set[str], identifier: Identifier) -> None:
        name = identifier.get_real_name()
        if name:
            tables.add(name)

    def _ensure_limit(self, sql: str) -> str:
        if re.search(r"\blimit\s+\d+\b", sql, flags=re.IGNORECASE):
            return sql
        return f"{sql} limit {self.default_limit}"
