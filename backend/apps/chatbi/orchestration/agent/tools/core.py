"""ChatBI 专属结束 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.errors import AgentFinalizationError
from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
    build_partial_finalization,
)
from apps.chatbi.services.generation.answer_composer import (
    AnswerComposer,
    AnswerComposerInput,
)
from apps.tool import ToolErrorCategory, ToolExecutionPolicy, ToolResult


class FinishArgs(BaseModel):
    """结束作答。必须已存在成功的 execute_sql 结果。"""


class FinishResult(BaseModel):
    answer: str
    chart: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    non_standard: bool = False
    claims: list[dict[str, Any]] = Field(default_factory=list)
    caliber_card: dict[str, Any] = Field(default_factory=dict)
    chart_spec: dict[str, Any] = Field(default_factory=dict)


class FinishTool(AgentTool):
    name = "finish"
    description = "结束本次问数，由独立模型生成分析回复和图表配置。必须先成功执行 execute_sql。"
    args_model = FinishArgs
    result_model = FinishResult
    # 最终收口包含两个结构化模型调用，需要独立于普通轻量工具保留足够时间。
    execution = ToolExecutionPolicy(timeout_seconds=60)

    def __init__(
        self,
        finalization_service: AgentFinalizationService | None = None,
        answer_composer: AnswerComposer | None = None,
    ) -> None:
        self._finalization_service = finalization_service
        self._answer_composer = answer_composer

    def execute(
        self,
        ctx: AgentToolContext,
        args: FinishArgs,
    ) -> ToolResult[FinishResult]:
        execution = ctx.state.get("last_execution")
        if not isinstance(execution, dict) or not execution:
            return ToolResult.rejected(
                "尚无成功的 execute_sql 结果，不能结束问数。",
                error_code="execution_required_before_finish",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if self._finalization_service is None and self._answer_composer is None:
            return ToolResult.rejected(
                "Agent 最终生成服务未配置。",
                error_code="agent_finalization_service_required",
                error_category=ToolErrorCategory.CONFIGURATION,
            )

        understanding = ctx.state.get("question_understanding")
        intent = understanding.get("intent", {}) if isinstance(understanding, dict) else {}
        full_data = ctx.state.get("full_data")
        question = str(
            ctx.state.get("question") or ctx.state.get("original_question") or ""
        )
        rows = full_data if isinstance(full_data, list) else []
        try:
            # legacy ReAct 保持原双模型收口；Composer 只对显式新模式或直接注入测试生效。
            use_composer = self._answer_composer is not None and (
                "execution_mode" not in ctx.state
                or ctx.state.get("execution_mode")
                in {"agent", "fast", "plan", "research"}
            )
            if use_composer:
                composed = self._answer_composer.compose(
                    AnswerComposerInput(
                        question=question,
                        intent=intent if isinstance(intent, dict) else {},
                        execution=execution,
                        rows=rows,
                        semantic_context=ctx.state.get("semantic_scope") if isinstance(ctx.state.get("semantic_scope"), dict) else {},
                        plan=ctx.state.get("analysis_plan") if isinstance(ctx.state.get("analysis_plan"), dict) else {},
                        mode="react_legacy",
                    )
                )
                result = composed
            else:
                result = self._finalization_service.generate(
                    AgentFinalizationInput(
                        question=question,
                        intent=intent if isinstance(intent, dict) else {},
                        execution=execution,
                        rows=rows,
                    )
                )
        except AgentFinalizationError as exc:
            # 查询已成功执行时收口失败必须降级为部分作答（表格直出），
            # 不能以 rejected 观察返回让模型重复调用 finish 形成死循环。
            result = build_partial_finalization(
                execution=execution,
                rows=rows,
                failed_stage=exc.error_code,
            )
        answer = result.answer
        if execution.get("sql_source") == "manual":
            answer += (
                "\n\n> 注：本次 SQL 由 AI 直接生成（非标准指标口径），"
                "结果口径可能与指标定义存在差异。"
            )
        data = FinishResult(
            answer=answer,
            chart=result.chart,
            sql=execution.get("sql"),
            non_standard=execution.get("sql_source") == "manual",
            claims=list(getattr(result, "claims", []) or []),
            caliber_card=dict(getattr(result, "caliber_card", {}) or {}),
            chart_spec=dict(getattr(result, "chart_spec", {}) or {}),
        )
        return ToolResult.succeeded("finish", data)


__all__ = ["FinishArgs", "FinishResult", "FinishTool"]
