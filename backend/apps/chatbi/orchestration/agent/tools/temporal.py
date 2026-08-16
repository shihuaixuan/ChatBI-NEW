"""Agent 时间解析工具。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.temporal import resolve_time_range
from apps.tool import (
    RetryAdvice,
    ToolConcurrency,
    ToolErrorCategory,
    ToolExecutionPolicy,
    ToolResult,
    json_summary,
)


class ParseTimeRangeArgs(BaseModel):
    """时间解析输入。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, min_length=1, description="单个原始时间表达")
    raws: list[str] = Field(default_factory=list, description="多个原始时间表达")

    @property
    def expressions(self) -> list[str]:
        """统一返回单区间和多区间输入。"""

        values = [self.raw] if self.raw else []
        values.extend(self.raws)
        return list(dict.fromkeys(value for value in values if value))


class ParseTimeRangeResult(BaseModel):
    """时间解析结果；unsupported 由 Agent 决定是否发起澄清。"""

    model_config = ConfigDict(extra="forbid")

    raw: str
    status: Literal["resolved", "unsupported", "not_provided"]
    normalized: dict[str, Any] | None = None
    ranges: list[dict[str, Any]] = Field(default_factory=list)


class ParseTimeRangeTool(AgentTool):
    """使用 Run 固定的时间上下文解析自然语言时间。"""

    name = "parse_time_range"
    description = (
        "解析已确认问题理解中的 time_range.raw，返回可执行的绝对时间范围。"
        "当时间表达存在时，应与 search_semantic_assets 在同一轮并发调用；"
        "不得自行修改用户时间。"
    )
    args_model = ParseTimeRangeArgs
    result_model = ParseTimeRangeResult
    execution = ToolExecutionPolicy(
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        timeout_seconds=5,
    )

    def execute(
        self,
        ctx: AgentToolContext,
        args: ParseTimeRangeArgs,
    ) -> ToolResult[ParseTimeRangeResult]:
        if ctx.temporal_context is None:
            return ToolResult.failed(
                "缺少本次 Run 固定的时间上下文，无法解析时间。",
                error_code="temporal_context_required",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        expressions = args.expressions
        if not expressions:
            return ToolResult.failed(
                "至少需要一个原始时间表达。",
                error_code="time_range_input_required",
                error_category=ToolErrorCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
            )
        ranges = [
            result
            for expression in expressions
            if (result := resolve_time_range(expression, ctx.temporal_context)) is not None
        ]
        normalized = ranges[0] if ranges else None
        status: Literal["resolved", "unsupported", "not_provided"]
        if normalized is None:
            status = "not_provided"
        elif normalized.get("kind") == "unsupported":
            status = "unsupported"
        else:
            status = "resolved"
        data = ParseTimeRangeResult(
            raw=expressions[0],
            status=status,
            normalized=normalized,
            ranges=ranges,
        )
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


__all__ = [
    "ParseTimeRangeArgs",
    "ParseTimeRangeResult",
    "ParseTimeRangeTool",
]
