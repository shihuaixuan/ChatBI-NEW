"""ChatBI 专属结束 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.chatbi.models import QueryFinalReplyProjectionData
from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.chatbi.services.generation import (
    FinalReplyProjectionError,
    project_query_final_reply,
)
from apps.tool import ToolErrorCategory, ToolExecutionPolicy, ToolResult


class FinishArgs(BaseModel):
    """结束作答。必须已存在成功的 execute_sql 结果。"""

    answer_markdown: str = Field(
        min_length=1,
        description="面向用户的最终回答（markdown）",
    )
    chart_type: Literal["table", "bar", "line", "pie"] | None = Field(
        default=None,
        description="推荐图表类型",
    )
    x_field: str | None = Field(default=None, description="x 轴或类别字段名")
    y_fields: list[str] = Field(default_factory=list, description="数值系列字段名")


class FinishResult(BaseModel):
    answer: str
    chart: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    non_standard: bool = False


class FinishTool(AgentTool):
    name = "finish"
    description = (
        "结束本次问数并给出最终回答与图表建议。只有在 execute_sql 成功拿到真实数据后才允许调用；"
        "没有数据时应如实说明失败原因。"
    )
    args_model = FinishArgs
    result_model = FinishResult
    execution = ToolExecutionPolicy(timeout_seconds=5)

    def execute(
        self,
        ctx: AgentToolContext,
        args: FinishArgs,
    ) -> ToolResult[FinishResult]:
        try:
            result = project_query_final_reply(
                QueryFinalReplyProjectionData(
                    answer_markdown=args.answer_markdown,
                    execution=ctx.state.get("last_execution"),
                    chart_type=args.chart_type,
                    x_field=args.x_field,
                    y_fields=args.y_fields,
                )
            )
        except FinalReplyProjectionError as exc:
            return ToolResult.rejected(
                str(exc),
                error_code=exc.error_code,
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        data = FinishResult.model_validate(result.model_dump(mode="json"))
        return ToolResult.succeeded("finish", data)


__all__ = ["FinishArgs", "FinishResult", "FinishTool"]
