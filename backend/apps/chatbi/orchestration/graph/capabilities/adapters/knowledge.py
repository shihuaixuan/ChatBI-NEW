"""Workflow 到统一检索核心的适配器。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models import SemanticRetrievalData
from apps.chatbi.orchestration.graph.capabilities.context import ChatBIRunContext
from apps.chatbi.orchestration.graph.capabilities.interactions import (
    apply_slot_response_to_intent,
)
from apps.chatbi.services.planning import SemanticRetrievalService
from apps.datasource import DatasourceQueryService, DatasourceQuerySubject
from apps.retrieval.errors import RetrievalConfigurationError, RetrievalQueryError
from apps.retrieval.query.service import RetrievalService
from apps.semantic.services.dataset_binding_service import (
    SemanticDatasetBindingService,
)


class SemanticKnowledgeAdapter:
    """把 Workflow 上下文转换为统一检索参数。"""

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        semantic_retrieval_service: SemanticRetrievalService | None = None,
        dataset_binding_service: SemanticDatasetBindingService | None = None,
        query_service: DatasourceQueryService | None = None,
    ) -> None:
        self._semantic_retrieval_service = semantic_retrieval_service
        if self._semantic_retrieval_service is None and retrieval_service is not None:
            self._semantic_retrieval_service = SemanticRetrievalService(
                retrieval_service
            )
        self._dataset_binding_service = dataset_binding_service
        self._query_service = query_service
        if (dataset_binding_service is None) != (query_service is None):
            raise ValueError("SEMANTIC_AUTHORIZATION_DEPENDENCIES_INCOMPLETE")

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        intent = apply_slot_response_to_intent(ctx.intent, ctx.slot_response)
        if self._semantic_retrieval_service is None:
            raise RetrievalConfigurationError(
                "Workflow 未配置统一检索服务",
                details={"reason_code": "RETRIEVAL_SERVICE_MISSING"},
            )
        if not ctx.question or ctx.dataset_id is None:
            raise RetrievalQueryError(
                "语义检索缺少问题或数据集",
                details={"reason_code": "SEMANTIC_BINDING_REQUEST_INCOMPLETE"},
            )
        package = self._semantic_retrieval_service.retrieve(
            SemanticRetrievalData(
                workspace_id=ctx.tenant_id,
                user_id=ctx.user_id,
                dataset_id=ctx.dataset_id,
                original_question=ctx.raw_question or ctx.question,
                rewritten_question=ctx.question,
                intent=intent,
                request_id=ctx.run_id or None,
            )
        )
        if self._dataset_binding_service is None or self._query_service is None:
            return package
        binding = self._dataset_binding_service.resolve_execution_binding(
            ctx.tenant_id,
            ctx.dataset_id,
        )
        policy = self._query_service.resolve_policy(
            DatasourceQuerySubject(
                workspace_id=ctx.tenant_id,
                user_id=ctx.user_id or 1,
            ),
            binding.datasource_id,
        )
        if not policy.allowed or not policy.authorized_tables:
            raise RetrievalQueryError(
                policy.reason or "没有可检索的授权表",
                details={
                    "reason_code": policy.error_code
                    or "SEMANTIC_TABLE_ACCESS_DENIED"
                },
            )
        return self._semantic_retrieval_service.filter_authorized_tables(
            package,
            policy.authorized_tables,
        )


__all__ = ["SemanticKnowledgeAdapter"]
