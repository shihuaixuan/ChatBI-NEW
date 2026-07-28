"""Semantic 公共 Tool。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from apps.tool.base import Tool, ToolExecutionPolicy, json_summary
from apps.tool.result import RetryAdvice, ToolErrorCategory, ToolResult
from apps.tool.tools.context import TrustedToolContext


class TermQueryService(Protocol):
    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[Any]: ...


class SearchTerminologyArgs(BaseModel):
    term: str = Field(min_length=1, description="要查询的业务术语或口语说法")


class SearchTerminologyResult(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class SearchTerminologyTool(
    Tool[TrustedToolContext, SearchTerminologyArgs, SearchTerminologyResult]
):
    name = "search_terminology"
    description = "查询业务术语的解释与映射，用于理解问题中的业务词、缩写和同义词。"
    args_model = SearchTerminologyArgs
    result_model = SearchTerminologyResult
    execution = ToolExecutionPolicy(timeout_seconds=20)

    def __init__(self, term_query_service: TermQueryService) -> None:
        if term_query_service is None:
            raise ValueError("SEMANTIC_TERM_QUERY_SERVICE_REQUIRED")
        self._term_query_service = term_query_service

    def execute(
        self,
        ctx: TrustedToolContext,
        args: SearchTerminologyArgs,
    ) -> ToolResult[SearchTerminologyResult]:
        if ctx.dataset_id is None or ctx.dataset_id <= 0:
            return ToolResult.failed(
                "当前问数记录没有绑定 Semantic 数据集，无法查询业务术语。",
                error_code="semantic_dataset_not_found",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        results = self._term_query_service.search(
            ctx.workspace_id,
            ctx.dataset_id,
            args.term,
            limit=10,
        )
        items = [result.model_dump(mode="json") for result in results]
        data = SearchTerminologyResult(items=items, count=len(items))
        if not results:
            return ToolResult.succeeded(
                f"术语库中未找到与「{args.term}」相关的条目。",
                data,
            )
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


__all__ = [
    "SearchTerminologyArgs",
    "SearchTerminologyResult",
    "SearchTerminologyTool",
    "TermQueryService",
]
