"""Datasource 公共 Tool。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.datasource import (
    DatasourceQueryErrorCategory,
    DatasourceQueryRequest,
    DatasourceQueryResult,
    DatasourceQueryService,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.tool.base import (
    Tool,
    ToolConcurrency,
    ToolExecutionPolicy,
    json_summary,
)
from apps.tool.result import (
    RetryAdvice,
    ToolErrorCategory,
    ToolResult,
)
from apps.tool.tools.context import TrustedToolContext


class PhysicalSchemaReader(Protocol):
    """读取已经按身份和数据权限过滤的物理结构。"""

    def get(
        self,
        datasource_id: int,
        *,
        subject: DatasourceQuerySubject,
        table_keyword: str = "",
    ) -> Any: ...


class GetDatasetSchemaArgs(BaseModel):
    table_keyword: str = Field(default="", description="可选，按表名/注释过滤")


class GetDatasetSchemaResult(BaseModel):
    tables: list[dict[str, Any]] = Field(default_factory=list)
    table_count: int = 0


class GetDatasetSchemaTool(
    Tool[TrustedToolContext, GetDatasetSchemaArgs, GetDatasetSchemaResult]
):
    name = "get_dataset_schema"
    description = (
        "查看当前数据源的物理表、字段、类型与注释。语义检索未覆盖时，"
        "可基于此结构编写只读 SQL。"
    )
    args_model = GetDatasetSchemaArgs
    result_model = GetDatasetSchemaResult
    execution = ToolExecutionPolicy(timeout_seconds=30)

    def __init__(self, schema_reader: PhysicalSchemaReader) -> None:
        if schema_reader is None:
            raise ValueError("PHYSICAL_SCHEMA_READER_REQUIRED")
        self._schema_reader = schema_reader

    def execute(
        self,
        ctx: TrustedToolContext,
        args: GetDatasetSchemaArgs,
    ) -> ToolResult[GetDatasetSchemaResult]:
        if ctx.datasource_id is None or ctx.user_id is None:
            return ToolResult.failed(
                "缺少数据源或可信用户身份，无法查看表结构。",
                error_code="query_subject_required",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        try:
            schema = self._schema_reader.get(
                ctx.datasource_id,
                subject=DatasourceQuerySubject(
                    user_id=ctx.user_id,
                    workspace_id=ctx.workspace_id,
                ),
                table_keyword=args.table_keyword,
            )
        except PermissionError as exc:
            return ToolResult.rejected(
                "当前身份无权读取该数据源的物理结构。",
                error_code=str(exc) or "physical_schema_access_denied",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        items = [
            {
                "table": table.name,
                "comment": table.comment,
                "fields": [
                    {
                        "name": field.name,
                        "type": field.data_type,
                        "comment": field.comment,
                    }
                    for field in table.fields
                ],
            }
            for table in schema.tables
        ]
        data = GetDatasetSchemaResult(tables=items, table_count=len(items))
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


class ValidateSqlArgs(BaseModel):
    sql: str = Field(min_length=1, description="待校验的 SQL")


class ValidateSqlResult(BaseModel):
    sql: str
    tables: list[str] = Field(default_factory=list)


class ValidateSqlTool(
    Tool[TrustedToolContext, ValidateSqlArgs, ValidateSqlResult]
):
    name = "validate_sql"
    description = (
        "校验 SQL 的只读性、单语句、表权限与资源上限，并自动补充 LIMIT。"
        "手写 SQL 在执行前应先调用。"
    )
    args_model = ValidateSqlArgs
    result_model = ValidateSqlResult
    execution = ToolExecutionPolicy(
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        timeout_seconds=10,
    )

    def __init__(self, query_service: DatasourceQueryService) -> None:
        if query_service is None:
            raise ValueError("DATASOURCE_QUERY_SERVICE_REQUIRED")
        self._query_service = query_service

    def execute(
        self,
        ctx: TrustedToolContext,
        args: ValidateSqlArgs,
    ) -> ToolResult[ValidateSqlResult]:
        request = _query_request(ctx, args.sql)
        if isinstance(request, ToolResult):
            return request
        result = self._query_service.validate(request)
        if result.status != DatasourceQueryStatus.SUCCEEDED or result.data is None:
            return _query_failure(result)
        data = ValidateSqlResult(sql=result.data.sql, tables=result.data.tables)
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


class ExecuteSqlArgs(BaseModel):
    sql: str = Field(min_length=1, description="要执行的只读 SQL")


class ExecuteSqlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str
    fields: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    stats_summary: dict[str, Any] = Field(default_factory=dict)
    execution_ms: int = 0


class ExecuteSqlTool(
    Tool[TrustedToolContext, ExecuteSqlArgs, ExecuteSqlResult]
):
    name = "execute_sql"
    description = (
        "执行只读 SQL 并返回样本行与统计摘要。内部会重新执行身份、表、行列权限和安全校验。"
    )
    args_model = ExecuteSqlArgs
    result_model = ExecuteSqlResult
    execution = ToolExecutionPolicy(timeout_seconds=60)

    def __init__(self, query_service: DatasourceQueryService) -> None:
        if query_service is None:
            raise ValueError("DATASOURCE_QUERY_SERVICE_REQUIRED")
        self._query_service = query_service

    def execute(
        self,
        ctx: TrustedToolContext,
        args: ExecuteSqlArgs,
    ) -> ToolResult[ExecuteSqlResult]:
        request = _query_request(ctx, args.sql)
        if isinstance(request, ToolResult):
            return request
        result = self._query_service.execute(request)
        if result.status != DatasourceQueryStatus.SUCCEEDED or result.data is None:
            return _query_failure(result)
        payload = result.data
        data = ExecuteSqlResult(
            sql=payload.sql,
            fields=payload.fields,
            sample_rows=payload.sample_rows,
            row_count=payload.row_count,
            stats_summary=payload.stats_summary,
            execution_ms=payload.execution_ms,
        )
        summary = data.model_dump(mode="json", exclude={"execution_ms"})
        return ToolResult.succeeded(
            json_summary(summary, ctx.summary_max_chars),
            data,
            metadata={
                "full_data": payload.full_data,
                "execution_metadata": payload.execution_metadata,
            },
        )


def _query_request(
    ctx: TrustedToolContext,
    sql: str,
) -> DatasourceQueryRequest | ToolResult[Any]:
    if ctx.datasource_id is None or ctx.user_id is None:
        return ToolResult.failed(
            "缺少数据源或可信用户身份。",
            error_code="query_subject_required",
            error_category=ToolErrorCategory.CONFIGURATION,
            retry_advice=RetryAdvice.NEVER,
        )
    return DatasourceQueryRequest(
        sql=sql,
        datasource_id=ctx.datasource_id,
        subject=DatasourceQuerySubject(
            user_id=ctx.user_id,
            workspace_id=ctx.workspace_id,
        ),
        selected_tables=ctx.selected_tables,
    )


def _query_failure(result: DatasourceQueryResult) -> ToolResult[Any]:
    category_map = {
        DatasourceQueryErrorCategory.AUTHORIZATION: ToolErrorCategory.AUTHORIZATION,
        DatasourceQueryErrorCategory.SAFETY: ToolErrorCategory.SAFETY,
        DatasourceQueryErrorCategory.VALIDATION: ToolErrorCategory.VALIDATION,
        DatasourceQueryErrorCategory.DOMAIN: ToolErrorCategory.DOMAIN,
        DatasourceQueryErrorCategory.TRANSIENT: ToolErrorCategory.TRANSIENT,
        DatasourceQueryErrorCategory.CONFIGURATION: ToolErrorCategory.CONFIGURATION,
    }
    category = category_map.get(result.error_category, ToolErrorCategory.DOMAIN)
    if result.status == DatasourceQueryStatus.REJECTED:
        return ToolResult.rejected(
            result.message or "查询被拒绝",
            error_code=result.error_code or "query_rejected",
            error_category=category,
        )
    return ToolResult.failed(
        result.message or "查询失败",
        error_code=result.error_code or "query_failed",
        error_category=category,
        retry_advice=RetryAdvice(result.retry_advice.value),
    )


__all__ = [
    "ExecuteSqlArgs",
    "ExecuteSqlResult",
    "ExecuteSqlTool",
    "GetDatasetSchemaArgs",
    "GetDatasetSchemaResult",
    "GetDatasetSchemaTool",
    "PhysicalSchemaReader",
    "ValidateSqlArgs",
    "ValidateSqlResult",
    "ValidateSqlTool",
]
