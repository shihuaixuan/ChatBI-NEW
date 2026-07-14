"""Workflow 到统一检索核心的兼容适配器。"""

from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.capabilities.context import ChatBIRunContext
from apps.chatbi_workflow.capabilities.interactions import apply_slot_response_to_intent
from apps.retrieval.legacy_headless import (
    CandidateGate,
    HeadlessDocumentRetriever,
)
from apps.retrieval.legacy_headless import (
    HeadlessKnowledgeAdapter as LegacyHeadlessKnowledgeAdapter,
)
from apps.retrieval.service import RetrievalService, build_semantic_binding_request


class HeadlessKnowledgeAdapter(LegacyHeadlessKnowledgeAdapter):
    """保留图侧原契约，并把 Workflow 上下文转换为检索核心参数。"""

    def __init__(
        self,
        *args: Any,
        retrieval_service: RetrievalService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._retrieval_service = retrieval_service

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        intent = apply_slot_response_to_intent(ctx.intent, ctx.slot_response)
        if self._retrieval_service is not None:
            if not ctx.question or ctx.dataset_id is None:
                return super().retrieve_semantic(
                    question=ctx.question,
                    dataset_id=ctx.dataset_id,
                    tenant_id=ctx.tenant_id,
                    intent=intent,
                )
            retrieval_request = build_semantic_binding_request(
                request_id=ctx.run_id or None,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.user_id or 1,
                dataset_id=ctx.dataset_id,
                original_question=ctx.raw_question or ctx.question,
                rewritten_question=ctx.question,
                intent=intent,
            )
            return self._retrieval_service.retrieve(retrieval_request).legacy_payload
        return super().retrieve_semantic(
            question=ctx.question,
            dataset_id=ctx.dataset_id,
            tenant_id=ctx.tenant_id,
            intent=intent,
        )


__all__ = ["CandidateGate", "HeadlessDocumentRetriever", "HeadlessKnowledgeAdapter"]
