"""P1 交互工具：clarify（终止动作）、search_terminology、get_sql_examples。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.agent.tools.base import (
    AgentTool,
    AgentToolContext,
    ToolOutput,
    json_summary,
)

SUMMARY_MAX_CHARS_DEFAULT = 4000


def _summary_limit(ctx: AgentToolContext) -> int:
    return getattr(ctx.config, "summary_max_chars", SUMMARY_MAX_CHARS_DEFAULT)


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


class ClarifyTool(AgentTool):
    """clarify 是终止动作：execute 只做结构校验，挂起/持久化由 AgentLoop 处理。"""

    name = "clarify"
    description = (
        "当歧义会影响 SQL 正确性时（指标口径二义、时间范围缺失、维度不明确），向用户澄清。"
        "必须给出结构化选项（来自语义包候选）。不要为可以合理默认的小事澄清。"
    )
    args_model = ClarifyArgs

    def execute(self, ctx: AgentToolContext, args: ClarifyArgs) -> ToolOutput:
        return ToolOutput(
            success=True,
            summary="clarify",
            payload={
                "question": args.question,
                "options": [option.model_dump() for option in args.options],
            },
        )


class SearchTerminologyArgs(BaseModel):
    term: str = Field(min_length=1, description="要查询的业务术语或口语说法")


class SearchTerminologyTool(AgentTool):
    name = "search_terminology"
    description = "查询业务术语的解释与映射（同义词、口径说明）。用于理解问题中的黑话/缩写。"
    args_model = SearchTerminologyArgs

    def execute(self, ctx: AgentToolContext, args: SearchTerminologyArgs) -> ToolOutput:
        from apps.terminology.curd.terminology import select_terminology_by_word

        results = select_terminology_by_word(ctx.session, args.term, ctx.oid, ctx.datasource_id) or []
        payload = {"items": results[:10], "count": len(results)}
        if not results:
            return ToolOutput(success=True, summary=f"术语库中未找到与「{args.term}」相关的条目。", payload=payload)
        return ToolOutput(success=True, summary=json_summary(payload, _summary_limit(ctx)), payload=payload)


class GetSqlExamplesArgs(BaseModel):
    question: str = Field(min_length=1, description="用于召回相似示例的问题文本")


class GetSqlExamplesTool(AgentTool):
    name = "get_sql_examples"
    description = (
        "召回与问题相似的历史问答 SQL 示例（few-shot 参考）。"
        "注意：示例仅供写 SQL 参考，不是真实查询结果，禁止当作答案。"
    )
    args_model = GetSqlExamplesArgs

    def execute(self, ctx: AgentToolContext, args: GetSqlExamplesArgs) -> ToolOutput:
        from apps.data_training.curd.data_training import select_training_by_question

        results = select_training_by_question(ctx.session, args.question, ctx.oid, ctx.datasource_id) or []
        payload = {"items": results[:5], "count": len(results), "note": "仅供参考，非真实结果"}
        if not results:
            return ToolOutput(success=True, summary="没有召回到相似的 SQL 示例。", payload=payload)
        return ToolOutput(success=True, summary=json_summary(payload, _summary_limit(ctx)), payload=payload)
