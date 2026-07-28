"""Datasource SQL 安全校验、权限改写和执行的统一入口。"""

from __future__ import annotations

from typing import Any, Protocol, cast

import sqlglot
from sqlglot import exp

from apps.datasource.models.dto.query import (
    DatasourceDriverResult,
    DatasourceQueryData,
    DatasourceQueryErrorCategory,
    DatasourceQueryPolicy,
    DatasourceQueryRequest,
    DatasourceQueryResult,
    DatasourceQueryRetryAdvice,
    DatasourceQuerySubject,
)
from apps.datasource.models.rules.sql_query import ReadOnlySQLRule


class DatasourceQueryPolicyProvider(Protocol):
    """Access Control 为查询入口提供明确权限范围。"""

    def resolve(
        self,
        subject: DatasourceQuerySubject,
        datasource_id: int,
    ) -> DatasourceQueryPolicy: ...


class DatasourceQueryExecutor(Protocol):
    """执行已经过安全校验的 SQL。"""

    def execute(self, datasource_id: int, sql: str) -> DatasourceDriverResult: ...


class DatasourceQueryService:
    """所有调用方共用的安全查询服务。"""

    def __init__(
        self,
        policy_provider: DatasourceQueryPolicyProvider,
        executor: DatasourceQueryExecutor,
        *,
        default_limit: int | None = 100,
        sample_rows: int = 10,
    ) -> None:
        if policy_provider is None:
            raise ValueError("DATASOURCE_QUERY_POLICY_PROVIDER_REQUIRED")
        if executor is None:
            raise ValueError("DATASOURCE_QUERY_EXECUTOR_REQUIRED")
        self._policy_provider = policy_provider
        self._executor = executor
        self._sql_rule = ReadOnlySQLRule(default_limit=default_limit)
        self._sample_rows = max(sample_rows, 0)

    def validate(self, request: DatasourceQueryRequest) -> DatasourceQueryResult:
        """解析权限并校验改写前后的 SQL，不执行驱动。"""

        prepared = self._prepare(request)
        if isinstance(prepared, DatasourceQueryResult):
            return prepared
        sql, tables, effective_tables = prepared
        return DatasourceQueryResult.succeeded(
            DatasourceQueryData(
                sql=sql,
                tables=sorted(tables),
                effective_tables=effective_tables,
            )
        )

    def resolve_policy(
        self,
        subject: DatasourceQuerySubject,
        datasource_id: int,
    ) -> DatasourceQueryPolicy:
        """供 Schema 和语义投影复用同一权限提供者。"""

        return self._policy_provider.resolve(subject, datasource_id)

    def execute(self, request: DatasourceQueryRequest) -> DatasourceQueryResult:
        """重新完成全部安全校验后执行 SQL。"""

        prepared = self._prepare(request)
        if isinstance(prepared, DatasourceQueryResult):
            return prepared
        sql, tables, effective_tables = prepared
        executed = self._executor.execute(request.datasource_id, sql)
        if not executed.succeeded:
            return DatasourceQueryResult.failed(
                executed.message or "SQL 执行失败",
                error_code=executed.error_code or "sql_execute_error",
                error_category=(
                    DatasourceQueryErrorCategory.TRANSIENT
                    if executed.transient
                    else DatasourceQueryErrorCategory.DOMAIN
                ),
                retry_advice=(
                    DatasourceQueryRetryAdvice.SAME_INPUT
                    if executed.transient
                    else DatasourceQueryRetryAdvice.CORRECT_INPUT
                ),
            )

        payload = executed.payload
        raw_fields = payload.get("fields") or []
        raw_rows = payload.get("data") or payload.get("rows") or []
        fields = (
            [str(field) for field in raw_fields]
            if isinstance(raw_fields, list)
            else []
        )
        rows = (
            [row for row in raw_rows if isinstance(row, dict)]
            if isinstance(raw_rows, list)
            else []
        )
        raw_row_count = payload.get("row_count")
        row_count = (
            raw_row_count
            if isinstance(raw_row_count, int) and not isinstance(raw_row_count, bool)
            else len(rows)
        )
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"fields", "data", "rows"}
        }
        return DatasourceQueryResult.succeeded(
            DatasourceQueryData(
                sql=sql,
                tables=sorted(tables),
                effective_tables=effective_tables,
                fields=fields,
                sample_rows=rows[: self._sample_rows],
                row_count=row_count,
                stats_summary=numeric_stats(fields, rows),
                full_data=rows,
                execution_ms=int(payload.get("execution_ms") or 0),
                execution_metadata=metadata,
            )
        )

    def _prepare(
        self,
        request: DatasourceQueryRequest,
    ) -> tuple[str, set[str], list[str]] | DatasourceQueryResult:
        policy = self._policy_provider.resolve(request.subject, request.datasource_id)
        if not policy.allowed:
            return DatasourceQueryResult.rejected(
                policy.reason or "没有数据源访问权限",
                error_code=policy.error_code or "datasource_access_denied",
                error_category=DatasourceQueryErrorCategory.AUTHORIZATION,
            )

        authorized = {
            table.lower(): table
            for table in policy.authorized_tables
            if table.strip()
        }
        if not authorized:
            return DatasourceQueryResult.rejected(
                "当前身份没有可访问的表",
                error_code="authorized_tables_empty",
                error_category=DatasourceQueryErrorCategory.AUTHORIZATION,
            )
        selected = {table.lower() for table in request.selected_tables if table.strip()}
        if not selected:
            return DatasourceQueryResult.rejected(
                "查询前必须先确定服务端可信的表范围",
                error_code="selected_tables_required",
                error_category=DatasourceQueryErrorCategory.SAFETY,
            )
        effective_keys = set(authorized).intersection(selected)
        if not effective_keys:
            return DatasourceQueryResult.rejected(
                "选中表不在当前身份的授权范围内",
                error_code="effective_tables_empty",
                error_category=DatasourceQueryErrorCategory.AUTHORIZATION,
            )
        effective_tables = sorted(authorized[key] for key in effective_keys)

        initial = self._sql_rule.validate(request.sql, set(effective_tables))
        if not initial.valid or initial.sql is None:
            return self._safety_rejection(initial.error_code, initial.message)
        column_error = self._denied_column(
            initial.sql,
            initial.tables,
            policy,
        )
        if column_error is not None:
            return column_error
        rewritten = self._apply_row_filters(initial.sql, initial.tables, policy)
        final = self._sql_rule.validate(rewritten, set(effective_tables))
        if not final.valid or final.sql is None:
            return self._safety_rejection(final.error_code, final.message)
        return final.sql, set(final.tables), effective_tables

    @staticmethod
    def _safety_rejection(
        error_code: str | None,
        message: str,
    ) -> DatasourceQueryResult:
        return DatasourceQueryResult.rejected(
            message or "SQL 安全校验未通过",
            error_code=error_code or "sql_validation_failed",
            error_category=DatasourceQueryErrorCategory.SAFETY,
        )

    @staticmethod
    def _denied_column(
        sql: str,
        query_tables: frozenset[str],
        policy: DatasourceQueryPolicy,
    ) -> DatasourceQueryResult | None:
        table_names = {table.lower() for table in query_tables}
        denied = {
            item.column.lower()
            for item in policy.denied_columns
            if item.table.lower() in table_names
        }
        if not denied:
            return None
        try:
            expression = cast(exp.Expression, sqlglot.parse_one(sql))
        except Exception:
            return DatasourceQueryService._safety_rejection(
                "sql_parse_failed",
                "SQL 无法解析",
            )
        for select in expression.find_all(exp.Select):
            for projection in select.selects:
                target = (
                    projection.this
                    if isinstance(projection, exp.Alias)
                    else projection
                )
                if isinstance(target, exp.Star) or (
                    isinstance(target, exp.Column) and target.is_star
                ):
                    return DatasourceQueryResult.rejected(
                        "查询包含全部字段，无法排除无访问权限的字段",
                        error_code="column_permission_denied",
                        error_category=(
                            DatasourceQueryErrorCategory.AUTHORIZATION
                        ),
                    )
        for column in expression.find_all(exp.Column):
            if column.name.lower() in denied:
                return DatasourceQueryResult.rejected(
                    f"字段 {column.name} 无访问权限",
                    error_code="column_permission_denied",
                    error_category=DatasourceQueryErrorCategory.AUTHORIZATION,
                )
        return None

    @staticmethod
    def _apply_row_filters(
        sql: str,
        query_tables: frozenset[str],
        policy: DatasourceQueryPolicy,
    ) -> str:
        filters = [
            item
            for item in policy.row_filters
            if item.table.lower() in {table.lower() for table in query_tables}
        ]
        if not filters:
            return sql
        try:
            expression = sqlglot.parse_one(sql)
            if not isinstance(expression, exp.Query):
                raise ValueError("DATASOURCE_QUERY_EXPRESSION_REQUIRED")
            for row_filter in filters:
                matching_tables = [
                    table
                    for table in expression.find_all(exp.Table)
                    if table.name.lower() == row_filter.table.lower()
                ]
                for table in matching_tables:
                    select = table.find_ancestor(exp.Select)
                    if select is None:
                        continue
                    alias = table.alias_or_name
                    condition = sqlglot.parse_one(
                        row_filter.condition,
                        into=exp.Condition,
                    )
                    condition = condition.transform(
                        lambda node, table_alias=alias: (
                            exp.column(node.name, table=table_alias)
                            if isinstance(node, exp.Column) and not node.table
                            else node
                        )
                    )
                    # 行策略必须加到物理表所在的查询层，不能错误地加到外层 CTE 查询。
                    select.where(condition, copy=False)
            return expression.sql()
        except Exception as exc:
            raise ValueError("DATASOURCE_ROW_POLICY_SQL_INVALID") from exc


def numeric_stats(
    fields: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """生成数值字段摘要。"""

    stats: dict[str, dict[str, float]] = {}
    for field in fields:
        values = [
            float(row[field])
            for row in rows
            if field in row
            and isinstance(row[field], (int, float))
            and not isinstance(row[field], bool)
        ]
        if values:
            total = sum(values)
            stats[field] = {
                "min": min(values),
                "max": max(values),
                "sum": total,
                "avg": total / len(values),
            }
    return stats


__all__ = [
    "DatasourceQueryExecutor",
    "DatasourceQueryPolicyProvider",
    "DatasourceQueryService",
    "numeric_stats",
]
