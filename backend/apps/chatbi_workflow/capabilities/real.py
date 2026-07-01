from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.capabilities.adapters.answer import AnswerAdapter
from apps.chatbi_workflow.capabilities.adapters.interaction import InteractionAdapter
from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    HeadlessKnowledgeAdapter,
)
from apps.chatbi_workflow.capabilities.adapters.question import QuestionAdapter
from apps.chatbi_workflow.capabilities.adapters.recommendation import (
    RecommendationAdapter,
)
from apps.chatbi_workflow.capabilities.adapters.sql import SqlAdapter
from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)


class RealChatBICapabilityGateway:
    """真实 ChatBI 能力网关，未接入的能力继续回退到占位实现。"""

    def __init__(
        self,
        question_adapter: QuestionAdapter | None = None,
        answer_adapter: AnswerAdapter | None = None,
        knowledge_adapter: HeadlessKnowledgeAdapter | None = None,
        interaction_adapter: InteractionAdapter | None = None,
        sql_adapter: SqlAdapter | None = None,
        recommendation_adapter: RecommendationAdapter | None = None,
        fallback_gateway: PlaceholderChatBICapabilityGateway | None = None,
    ) -> None:
        self._question_adapter = question_adapter or QuestionAdapter()
        self._answer_adapter = answer_adapter or AnswerAdapter()
        self._knowledge_adapter = knowledge_adapter or HeadlessKnowledgeAdapter()
        self._interaction_adapter = interaction_adapter or InteractionAdapter()
        self._sql_adapter = sql_adapter or SqlAdapter()
        self._recommendation_adapter = recommendation_adapter or RecommendationAdapter()
        self._fallback_gateway = fallback_gateway or PlaceholderChatBICapabilityGateway()

    def invoke(
        self,
        capability: str,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if capability == "question.classify":
            return self._question_adapter.classify(request)
        if capability == "question.rewrite":
            return self._question_adapter.rewrite(request)
        if capability == "intent.recognize":
            return self._question_adapter.recognize_intent(request)
        if capability == "knowledge.retrieve":
            return self._knowledge_adapter.retrieve(request)
        if capability == "interaction.ask_rewrite_clarification":
            return self._interaction_adapter.ask_rewrite_clarification(request)
        if capability == "interaction.ask_intent_clarification":
            return self._interaction_adapter.ask_intent_clarification(request)
        if capability == "interaction.ask_slot_clarification":
            return self._interaction_adapter.ask_slot_clarification(request)
        if capability == "interaction.ask_metric_selection":
            return self._interaction_adapter.ask_metric_selection(request)
        if capability == "interaction.ask_cross_model_split":
            return self._interaction_adapter.ask_cross_model_split(request)
        if capability == "sql.generate":
            return self._sql_adapter.generate(request)
        if capability == "sql.execute":
            return self._sql_adapter.execute(request)
        if capability == "sql.execute_split":
            return self._sql_adapter.execute_split(request)
        if capability == "sql.handle_error":
            return self._sql_adapter.handle_error(request)
        if capability == "answer.reject":
            return self._answer_adapter.reject(request)
        if capability == "answer.chitchat":
            return self._answer_adapter.chitchat(request)
        if capability == "answer.generate":
            return self._answer_adapter.generate(request)
        if capability == "question.recommend":
            return self._recommendation_adapter.recommend(request)
        if capability == "answer.compose":
            return self._answer_adapter.compose(request)
        return self._fallback_gateway.invoke(capability, request, idempotency_key)
