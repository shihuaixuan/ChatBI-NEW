"""Workflow 到统一检索核心的适配器。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.orchestration.graph.capabilities.context import ChatBIRunContext
from apps.datasource import DatasourceQueryService, DatasourceQuerySubject
from apps.retrieval import (
    RetrievalConfigurationError,
    RetrievalQueryError,
    RetrievalService,
    build_retrieval_request,
    filter_semantic_payload_tables,
)
from apps.retrieval.models.dto import RetrievalBindingRequest, RetrievalIntent
from apps.semantic.services.dataset_binding_service import (
    SemanticDatasetBindingService,
)


class SemanticKnowledgeAdapter:
    """把 Workflow 上下文转换为统一检索参数。"""

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        dataset_binding_service: SemanticDatasetBindingService | None = None,
        query_service: DatasourceQueryService | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._dataset_binding_service = dataset_binding_service
        self._query_service = query_service
        if (dataset_binding_service is None) != (query_service is None):
            raise ValueError("SEMANTIC_AUTHORIZATION_DEPENDENCIES_INCOMPLETE")

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
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
        rewrite = ctx.rewrite
        # 兼容旧重写模型只返回原问题的结果，指标短语退回已校验的意图提及。
        metric_phrases = list(rewrite.get("metric_phrases") or [])
        if not metric_phrases:
            metric_phrases = [
                str(item)
                for item in list(ctx.intent.get("metric_mentions") or [])
                if str(item).strip()
            ]
        retrieval_request = build_retrieval_request(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.user_id or 1,
            dataset_id=ctx.dataset_id,
            metric_phrases=metric_phrases,
            dimension_phrases=list(rewrite.get("dimension_phrases") or []),
            request_id=ctx.run_id or None,
        )
        intent_payload = ctx.intent
        intent = RetrievalIntent.model_validate(
            {
                "intent_type": str(intent_payload.get("intent_type") or "metric_query"),
                "dimension_slots": intent_payload.get("dimension_slots") or [],
                "time_range": intent_payload.get("time_range") or {},
                "time_ranges": intent_payload.get("time_ranges") or [],
                "filter_mentions": intent_payload.get("filter_mentions") or [],
                "required_slot_types": intent_payload.get("required_slot_types") or [],
                "query_shape": intent_payload.get("query_shape") or {},
                "subject_domain": intent_payload.get("subject_domain") or {},
                "mention_graph": intent_payload.get("mention_graph"),
                "decomposition_queries": intent_payload.get("decomposition_queries") or [],
            }
        )
        binding_request = RetrievalBindingRequest(
            request_id=retrieval_request.request_id,
            tenant_id=retrieval_request.tenant_id,
            actor_id=retrieval_request.actor_id,
            original_question=ctx.raw_question,
            rewrite_question=ctx.question,
            candidate_request=retrieval_request,
            intent=intent,
        )
        package = self._retrieval_service.retrieve_and_bind(binding_request).payload
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
        return filter_semantic_payload_tables(
            package,
            policy.authorized_tables,
        )


__all__ = ["SemanticKnowledgeAdapter"]
