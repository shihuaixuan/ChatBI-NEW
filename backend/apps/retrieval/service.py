"""Graph 与 Agent 共用的 semantic-binding 检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from apps.retrieval.embedding import EmbeddingProvider
from apps.retrieval.errors import RetrievalQueryError
from apps.retrieval.profiles import get_retrieval_profile
from apps.retrieval.schemas import (
    RetrievalBundle,
    RetrievalDimensionSlot,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.semantic_binding import SemanticBindingRunner
from apps.retrieval.semantic_runtime import RetrievalEmbeddingRuntimeConfig
from apps.semantic.services.schema_service import DatasetSchemaProvider
from common.core.config import settings


@dataclass(frozen=True, slots=True)
class RetrievalServiceResult:
    """同时承载统一契约与现有调用方兼容结果。"""

    payload: dict[str, Any]
    bundle: RetrievalBundle
    filters: dict[str, Any]


class RetrievalService:
    """统一执行 semantic-binding，不保留旧策略运行分支。"""

    def __init__(
        self,
        session: Any,
        *,
        schema_provider: DatasetSchemaProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_config: RetrievalEmbeddingRuntimeConfig | None = None,
        semantic_binding_runner: SemanticBindingRunner | None = None,
        query_timeout_ms: int | None = None,
    ) -> None:
        self._session = session
        self._runner = semantic_binding_runner or SemanticBindingRunner(
            schema_provider=schema_provider,
            embedding_provider=embedding_provider,
            embedding_config=embedding_config,
        )
        self._query_timeout_ms = (
            settings.RETRIEVAL_QUERY_TIMEOUT_MS
            if query_timeout_ms is None
            else query_timeout_ms
        )
        if self._query_timeout_ms <= 0:
            raise ValueError("RETRIEVAL_QUERY_TIMEOUT_MS 必须大于 0")

    def retrieve(self, request: RetrievalRequest) -> RetrievalServiceResult:
        """执行语义绑定策略，并返回 Graph/Agent 可直接消费的 payload。"""

        self._validate_request(request)
        execution = self._runner.run(
            self._session,
            request,
            request.strategy_version,
            self._query_timeout_ms,
        )
        return RetrievalServiceResult(
            payload=execution.payload,
            bundle=execution.bundle,
            filters=execution.filters,
        )

    @staticmethod
    def _validate_request(request: RetrievalRequest) -> None:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise RetrievalQueryError("RetrievalService 仅支持 semantic_binding profile")
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        if request.strategy_version != profile.version:
            raise RetrievalQueryError(
                "RetrievalService 仅支持 semantic-binding",
                details={
                    "reason_code": "SEMANTIC_BINDING_STRATEGY_VERSION_UNSUPPORTED",
                    "strategy_version": request.strategy_version,
                },
            )
        if len(request.scope.dataset_ids) != 1:
            raise RetrievalQueryError("semantic_binding 检索必须且只能指定一个 dataset_id")


def build_semantic_binding_request(
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
    original_question: str,
    rewritten_question: str,
    intent: dict[str, Any] | None,
    request_id: str | None = None,
    principal_roles: list[str] | None = None,
    principal_role_ids: list[int] | None = None,
    permission_version: str | None = None,
    source_ids: list[str] | None = None,
    strategy_version: str | None = None,
) -> RetrievalRequest:
    """把已确认问题理解投影为语义绑定检索请求。"""

    source_intent = intent or {}
    intent_fields = RetrievalIntent.model_fields
    intent_payload = {
        key: value for key, value in source_intent.items() if key in intent_fields
    }
    raw_dimension_slots = source_intent.get("dimension_slots")
    if isinstance(raw_dimension_slots, list):
        retrieval_slot_fields = set(RetrievalDimensionSlot.model_fields)
        source_slot_fields = retrieval_slot_fields | {"value_confidence"}
        projected_slots: list[dict[str, Any]] = []
        for index, slot in enumerate(raw_dimension_slots):
            if not isinstance(slot, dict):
                raise RetrievalQueryError(
                    "dimension_slots 必须包含对象",
                    details={"reason_code": "DIMENSION_SLOT_INVALID", "slot_index": index},
                )
            unknown_fields = set(slot) - source_slot_fields
            if unknown_fields:
                raise RetrievalQueryError(
                    "dimension_slots 包含检索边界未定义的字段",
                    details={
                        "reason_code": "DIMENSION_SLOT_FIELDS_UNSUPPORTED",
                        "slot_index": index,
                        "fields": sorted(unknown_fields),
                    },
                )
            # value_confidence 属于问题理解诊断，不参与检索规划和门控。
            projected_slots.append(
                {key: value for key, value in slot.items() if key in retrieval_slot_fields}
            )
        intent_payload["dimension_slots"] = projected_slots
    intent_payload.setdefault(
        "intent_type",
        str(source_intent.get("intent_type") or "metric_query"),
    )
    profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
    return RetrievalRequest(
        request_id=request_id or f"retrieval-{uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        original_question=original_question,
        rewritten_question=rewritten_question,
        intent=RetrievalIntent.model_validate(intent_payload),
        scope=RetrievalScope(
            dataset_ids=[dataset_id],
            source_ids=source_ids or [],
            principal_roles=principal_roles or [],
            principal_role_ids=principal_role_ids or [],
            permission_version=permission_version,
        ),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=strategy_version or profile.version,
    )


def build_retrieval_service(
    session: Any,
    *,
    schema_provider: DatasetSchemaProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_config: RetrievalEmbeddingRuntimeConfig | None = None,
) -> RetrievalService:
    """应用组装层共用的 RetrievalService 工厂。"""

    return RetrievalService(
        session,
        schema_provider=schema_provider,
        embedding_provider=embedding_provider,
        embedding_config=embedding_config,
    )


__all__ = [
    "RetrievalService",
    "RetrievalServiceResult",
    "build_retrieval_service",
    "build_semantic_binding_request",
]
