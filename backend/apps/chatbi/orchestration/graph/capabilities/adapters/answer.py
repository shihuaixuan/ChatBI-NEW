from __future__ import annotations

from typing import Any

from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.models import (
    AnswerGenerationData,
    AnswerGenerationMode,
    AnswerProjectionData,
    FinalReplyProjectionData,
)
from apps.chatbi.orchestration.graph.capabilities.context import ChatBIRunContext
from apps.chatbi.services.generation import (
    AnswerGenerationService,
    AnswerModelClient,
    CallableAnswerModelClient,
    project_answer_context,
    project_final_reply,
)
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationService,
)
from apps.chatbi.services.understanding import StructuredModelService


def build_answer_projection(request: dict[str, Any]) -> dict[str, Any]:
    """读取 Graph 上下文并调用 ChatBI 回答上下文投影。"""

    ctx = ChatBIRunContext(request)
    return project_answer_context(
        AnswerProjectionData(
            raw_question=ctx.raw_question,
            rewritten_question=ctx.question,
            plan=ctx.plan,
            execution=ctx.execution,
            knowledge=ctx.knowledge,
            node_failure=ctx.node_failure,
            sql_error=ctx.sql_error,
        )
    ).payload


class AnswerAdapter:
    """ChatBI v1 回复节点真实能力适配器。"""

    def __init__(
        self,
        model_client: AnswerModelClient | None = None,
        answer_generation_service: AnswerGenerationService | None = None,
        finalization_service: AgentFinalizationService | None = None,
    ) -> None:
        if model_client is not None and answer_generation_service is not None:
            raise ValueError("ANSWER_MODEL_SOURCE_CONFLICT")
        model_service: StructuredModelService | None = None
        if answer_generation_service is not None:
            self._answer_generation_service = answer_generation_service
        elif model_client is not None:
            model_service = StructuredModelService(CallableAnswerModelClient(model_client))
            self._answer_generation_service = AnswerGenerationService(
                model_service
            )
        else:
            model_service = build_question_model_service()
            self._answer_generation_service = AnswerGenerationService(
                model_service
            )
        self._finalization_service = finalization_service or AgentFinalizationService(
            model_service or build_question_model_service()
        )

    def reject(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成拒绝回复，模型不可用时返回稳定安全文案。"""

        return self._generate("reject", request)

    def chitchat(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成闲聊回复，模型不可用时返回固定引导文案。"""

        return self._generate("chitchat", request)

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成业务回答，模型不可用时返回稳定降级文案。"""

        ctx = ChatBIRunContext(request)
        if ctx.execution.get("status") == "succeeded":
            result = self._finalization_service.generate(
                AgentFinalizationInput(
                    question=ctx.question,
                    intent=ctx.intent,
                    execution=ctx.execution,
                    rows=_execution_rows(ctx.execution),
                )
            )
            return {
                "answer": result.answer,
                "warnings": [],
                "render_type": "text",
                "citations": [],
                "chart": result.chart,
            }
        return self._generate("generate", request)

    def compose(self, request: dict[str, Any]) -> dict[str, Any]:
        """本地合成最终回复，保持前端响应契约稳定。"""

        ctx = ChatBIRunContext(request)
        return project_final_reply(
            FinalReplyProjectionData(
                answer=ctx.answer,
                recommendations=ctx.recommendations,
                chart=(
                    ctx.answer["chart"]
                    if "chart" in ctx.answer and isinstance(ctx.answer["chart"], dict)
                    else ctx.image_profile
                ),
            )
        ).model_dump(mode="json")

    def _generate(
        self,
        mode: AnswerGenerationMode,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        return self._answer_generation_service.generate(
            AnswerGenerationData(
                mode=mode,
                question=ctx.raw_question,
                projection=build_answer_projection(request),
            )
        ).model_dump(mode="json")


def _execution_rows(execution: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 SQL 执行结果中的完整行，兼容旧结果仅保存 sample_rows 的结构。"""

    rows = execution.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    collected: list[dict[str, Any]] = []
    for result in execution.get("results") or []:
        if not isinstance(result, dict):
            continue
        candidate = result.get("rows") or result.get("sample_rows") or []
        if isinstance(candidate, list):
            collected.extend(row for row in candidate if isinstance(row, dict))
    return collected
