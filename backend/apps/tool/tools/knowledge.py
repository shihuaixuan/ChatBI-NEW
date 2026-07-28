"""Knowledge 公共 Tool。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.tool.base import Tool, ToolExecutionPolicy, json_summary
from apps.tool.result import ToolResult
from apps.tool.tools.context import TrustedToolContext


class SqlExampleQueryService(Protocol):
    def search(
        self,
        question: str,
        workspace_id: int,
        *,
        datasource_id: int | None = None,
        assistant_id: int | None = None,
    ) -> list[dict[str, str]]: ...


class GetSqlExamplesArgs(BaseModel):
    question: str = Field(min_length=1, description="用于召回相似示例的问题文本")


class GetSqlExamplesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, str]] = Field(default_factory=list)
    count: int = 0
    note: str


class GetSqlExamplesTool(
    Tool[TrustedToolContext, GetSqlExamplesArgs, GetSqlExamplesResult]
):
    name = "get_sql_examples"
    description = (
        "召回与问题相似的历史问答 SQL 示例。示例只用于编写 SQL，不能作为真实查询结果。"
    )
    args_model = GetSqlExamplesArgs
    result_model = GetSqlExamplesResult
    execution = ToolExecutionPolicy(timeout_seconds=20)

    def __init__(self, query_service: SqlExampleQueryService) -> None:
        if query_service is None:
            raise ValueError("SQL_EXAMPLE_QUERY_SERVICE_REQUIRED")
        self._query_service = query_service

    def execute(
        self,
        ctx: TrustedToolContext,
        args: GetSqlExamplesArgs,
    ) -> ToolResult[GetSqlExamplesResult]:
        results = self._query_service.search(
            args.question,
            ctx.workspace_id,
            datasource_id=ctx.datasource_id,
        )
        data = GetSqlExamplesResult(
            items=results[:5],
            count=len(results),
            note="仅供参考，非真实结果",
        )
        if not results:
            return ToolResult.succeeded("没有召回到相似的 SQL 示例。", data)
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


__all__ = [
    "GetSqlExamplesArgs",
    "GetSqlExamplesResult",
    "GetSqlExamplesTool",
    "SqlExampleQueryService",
]
