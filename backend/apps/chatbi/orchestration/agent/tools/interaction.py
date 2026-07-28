"""ChatBI 澄清控制 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.tool import (
    ToolExecutionPolicy,
    ToolResult,
)


class ClarifyOption(BaseModel):
    label: str = Field(description="展示给用户的选项文案")
    value: str = Field(description="选项值")
    asset_id: int | None = Field(default=None, description="选项对应的语义资产 id（如指标候选）")


class ClarifyArgs(BaseModel):
    """向用户澄清关键歧义。这是终止动作：本轮暂停，等待用户回答后继续。"""

    question: str = Field(min_length=1, description="向用户提出的澄清问题")
    options: list[ClarifyOption] = Field(
        default_factory=list,
        description="结构化选项（强烈建议提供，来自语义包候选）；为空表示自由文本澄清",
    )


class ClarifyResult(BaseModel):
    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)


class ClarifyTool(AgentTool):
    """clarify 是终止动作：execute 只做结构校验，挂起和持久化由 Agent 编排层处理。"""

    name = "clarify"
    description = (
        "当歧义会影响 SQL 正确性时（指标口径二义、时间范围缺失、维度不明确），向用户澄清。"
        "必须给出结构化选项（来自语义包候选）。不要为可以合理默认的小事澄清。"
    )
    args_model = ClarifyArgs
    result_model = ClarifyResult
    execution = ToolExecutionPolicy(timeout_seconds=5)

    def execute(
        self,
        ctx: AgentToolContext,
        args: ClarifyArgs,
    ) -> ToolResult[ClarifyResult]:
        return ToolResult.succeeded(
            "clarify",
            ClarifyResult(
                question=args.question,
                options=[option.model_dump() for option in args.options],
            ),
        )
