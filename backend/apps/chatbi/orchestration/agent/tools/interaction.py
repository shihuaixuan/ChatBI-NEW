"""P1 交互工具：clarify（终止动作）、search_terminology、get_sql_examples。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.tool import ToolOutput, json_summary
from apps.knowledge.composition import build_sql_example_query_service

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
    is_read_only = False
    is_concurrency_safe = False

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
    is_read_only = True
    # 共享 Session 下禁止并行读。
    is_concurrency_safe = False

    def execute(self, ctx: AgentToolContext, args: SearchTerminologyArgs) -> ToolOutput:
        dataset_id = ctx.dataset_id or ctx.state.get("dataset_id")
        if not isinstance(dataset_id, int) or dataset_id <= 0:
            return ToolOutput(
                success=False,
                summary="当前问数记录没有绑定 Semantic 数据集，无法查询业务术语。",
                error_code="semantic_dataset_not_found",
            )
        if ctx.term_query_service is None:
            return ToolOutput(
                success=False,
                summary="Semantic 术语查询服务未装配。",
                error_code="semantic_term_query_unavailable",
            )

        results = ctx.term_query_service.search(
            ctx.oid,
            dataset_id,
            args.term,
            limit=10,
        )
        items = [result.model_dump() for result in results]
        payload = {"items": items, "count": len(items)}
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
    is_read_only = True
    # 内部会 build_*_service(ctx.session)，共享 Session 下禁止并行。
    is_concurrency_safe = False

    def execute(self, ctx: AgentToolContext, args: GetSqlExamplesArgs) -> ToolOutput:
        results = build_sql_example_query_service(ctx.session).search(
            args.question,
            ctx.oid,
            datasource_id=ctx.datasource_id,
        )
        payload = {"items": results[:5], "count": len(results), "note": "仅供参考，非真实结果"}
        if not results:
            return ToolOutput(success=True, summary="没有召回到相似的 SQL 示例。", payload=payload)
        return ToolOutput(success=True, summary=json_summary(payload, _summary_limit(ctx)), payload=payload)
