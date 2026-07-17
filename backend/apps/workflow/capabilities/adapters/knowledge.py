"""Workflow 到统一检索核心的适配器。"""

from __future__ import annotations

from typing import Any

from apps.workflow.capabilities.context import ChatBIRunContext
from apps.workflow.capabilities.interactions import apply_slot_response_to_intent
from apps.retrieval.errors import RetrievalConfigurationError, RetrievalQueryError
from apps.retrieval.service import RetrievalService, build_semantic_binding_request


class SemanticKnowledgeAdapter:
    """把 Workflow 上下文转换为统一检索参数。"""

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        intent = apply_slot_response_to_intent(ctx.intent, ctx.slot_response)
        if self._retrieval_service is None:
            raise RetrievalConfigurationError(
                "Workflow 未配置统一检索服务",
                details={"reason_code": "RETRIEVAL_SERVICE_MISSING"},
            )
        if not ctx.question or ctx.dataset_id is None:
            raise RetrievalQueryError(
                "语义检索缺少问题或数据集",
                details={"reason_code": "SEMANTIC_BINDING_REQUEST_INCOMPLETE"},
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
        return self._retrieval_service.retrieve(retrieval_request).payload


__all__ = ["SemanticKnowledgeAdapter"]
