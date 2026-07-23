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
    ) -> None:
        if model_client is not None and answer_generation_service is not None:
            raise ValueError("ANSWER_MODEL_SOURCE_CONFLICT")
        if answer_generation_service is not None:
            self._answer_generation_service = answer_generation_service
        elif model_client is not None:
            self._answer_generation_service = AnswerGenerationService(
                StructuredModelService(CallableAnswerModelClient(model_client))
            )
        else:
            self._answer_generation_service = AnswerGenerationService(
                build_question_model_service()
            )

    def reject(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成拒绝回复，模型不可用时返回稳定安全文案。"""

        return self._generate("reject", request)

    def chitchat(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成闲聊回复，模型不可用时返回固定引导文案。"""

        return self._generate("chitchat", request)

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成业务回答，模型不可用时返回稳定降级文案。"""

        return self._generate("generate", request)

    def compose(self, request: dict[str, Any]) -> dict[str, Any]:
        """本地合成最终回复，保持前端响应契约稳定。"""

        ctx = ChatBIRunContext(request)
        return project_final_reply(
            FinalReplyProjectionData(
                answer=ctx.answer,
                recommendations=ctx.recommendations,
                chart=ctx.image_profile,
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
