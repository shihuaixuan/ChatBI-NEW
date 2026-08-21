"""Graph 与 Agent 共用的 semantic-binding 检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from apps.retrieval.embedding import EmbeddingProvider
from apps.retrieval.errors import RetrievalQueryError
from apps.retrieval.models.dto import (
    RetrievalBindingRequest,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.query.hybrid import HybridRecallResult
from apps.retrieval.query.profiles import get_retrieval_profile
from apps.retrieval.query.semantic_binding import (
    SemanticBindingRunner,
)
from apps.retrieval.query.semantic_runtime import RetrievalEmbeddingRuntimeConfig
from apps.semantic.services.schema_service import DatasetSchemaProvider
from common.core.config import settings


@dataclass(frozen=True, slots=True)
class RetrievalServiceResult:
    """候选资产检索结果。"""

    payload: dict[str, Any]
    recall: HybridRecallResult
    filters: dict[str, Any]


class RetrievalService:
    """统一执行语义资产候选检索，不进入候选绑定。"""

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

    def retrieve(
        self,
        request: RetrievalRequest,
        *,
        timeout_ms: int | None = None,
    ) -> RetrievalServiceResult:
        """只根据指标和维度短语召回候选资产。"""

        self._validate_request(request)
        effective_timeout_ms = min(
            self._query_timeout_ms,
            timeout_ms if timeout_ms is not None else self._query_timeout_ms,
        )
        if effective_timeout_ms <= 0:
            raise TimeoutError("RETRIEVAL_DEADLINE_EXCEEDED")
        execution = self._runner.retrieve_candidates(
            self._session,
            request,
            request.strategy_version,
            effective_timeout_ms,
        )
        return RetrievalServiceResult(
            payload=execution.payload,
            recall=execution.recall,
            filters=execution.filters,
        )

    def retrieve_and_bind(
        self,
        request: RetrievalBindingRequest,
        *,
        timeout_ms: int | None = None,
    ) -> RetrievalServiceResult:
        """执行候选召回和已治理资产绑定，供 Graph/Agent 的语义入口使用。"""

        self._validate_request(request.candidate_request)
        effective_timeout_ms = min(
            self._query_timeout_ms,
            timeout_ms if timeout_ms is not None else self._query_timeout_ms,
        )
        if effective_timeout_ms <= 0:
            raise TimeoutError("RETRIEVAL_DEADLINE_EXCEEDED")
        execution = self._runner.retrieve_candidates(
            self._session,
            request.candidate_request,
            request.strategy_version,
            effective_timeout_ms,
        )
        bound = self._runner.bind(
            self._session,
            request,
            execution.recall,
            effective_timeout_ms,
        )
        return RetrievalServiceResult(
            payload=bound.payload,
            recall=execution.recall,
            filters=bound.filters,
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


def build_retrieval_request(
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
    metric_phrases: list[str],
    dimension_phrases: list[str],
    request_id: str | None = None,
    principal_roles: list[str] | None = None,
    principal_role_ids: list[int] | None = None,
    permission_version: str | None = None,
    source_ids: list[str] | None = None,
    strategy_version: str | None = None,
) -> RetrievalRequest:
    """把问题重写模型输出的短语投影为候选资产检索请求。"""

    profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
    return RetrievalRequest(
        request_id=request_id or f"retrieval-{uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        metric_phrases=metric_phrases,
        dimension_phrases=dimension_phrases,
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
    "build_retrieval_request",
]
