"""semantic-binding 的独立执行入口。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from apps.retrieval.embedding import (
    EmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.models.dto import (
    RetrievalBundle,
    RetrievalProfileName,
    RetrievalRequest,
)
from apps.retrieval.projection.payload import bundle_to_semantic_payload
from apps.retrieval.query.hybrid import (
    HybridRetrievalConfig,
    SemanticBindingHybridRetriever,
)
from apps.retrieval.query.policy import (
    SemanticBindingPolicy,
    bind_default_time_dimensions,
)
from apps.retrieval.query.profiles import get_retrieval_profile
from apps.retrieval.query.semantic_runtime import (
    ObservedEmbeddingProvider,
    RetrievalEmbeddingRuntimeConfig,
    RetrievalRerankRuntimeConfig,
)
from apps.retrieval.reranking import SiliconFlowReranker
from apps.semantic.composition import build_semantic_schema_service
from apps.semantic.services.schema_service import (
    DatasetSchemaProvider,
)
from common.core.config import settings

SEMANTIC_BINDING_STRATEGY_VERSION = "semantic-binding"


class SemanticBindingExecutionResult(BaseModel):
    """语义绑定对统一契约和现有业务契约的一次性执行结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle: RetrievalBundle
    payload: dict[str, Any]
    filters: dict[str, Any] = Field(default_factory=dict)


class SemanticBindingRunner:
    """在调用方提供的 session 内执行语义绑定，并投影为现有业务契约。"""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_config: RetrievalEmbeddingRuntimeConfig | None = None,
        hybrid_config: HybridRetrievalConfig | None = None,
        policy: SemanticBindingPolicy | None = None,
        rerank_config: RetrievalRerankRuntimeConfig | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._embedding_config = embedding_config or RetrievalEmbeddingRuntimeConfig.from_settings(
            settings
        )
        self._embedding_startup_error: RetrievalConfigurationError | None = None
        if self._embedding_config.enabled:
            try:
                self._embedding_config.validate()
                if (
                    self._embedding_provider is None
                    and self._embedding_config.provider
                    not in {"openai_compatible", "sentence_transformers"}
                ):
                    raise RetrievalConfigurationError(
                        "不支持的语义绑定 embedding provider",
                        details={"reason_code": "EMBEDDING_PROVIDER_UNSUPPORTED"},
                    )
            except RetrievalConfigurationError as exc:
                if not self._embedding_config.allow_lexical_fallback:
                    raise
                self._embedding_startup_error = exc
        dense_error_code = (
            str(
                self._embedding_startup_error.details.get("reason_code")
                or self._embedding_startup_error.code
            )
            if self._embedding_startup_error is not None
            else None
        )
        self._hybrid_config = hybrid_config or HybridRetrievalConfig(
            embedding_dimension=self._embedding_config.dimension,
            dense_enabled=self._embedding_config.enabled,
            dense_unavailable_error_code=dense_error_code,
        )
        if hybrid_config is not None and dense_error_code is not None:
            self._hybrid_config = replace(
                hybrid_config,
                dense_unavailable_error_code=dense_error_code,
            )
        self._policy = policy
        self._rerank_config = rerank_config or RetrievalRerankRuntimeConfig.from_settings(
            settings
        )
        self._schema_provider = schema_provider

    def run(
        self,
        session: Any,
        request: RetrievalRequest,
        strategy_version: str,
        timeout_ms: int,
    ) -> SemanticBindingExecutionResult:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise ValueError("SEMANTIC_BINDING_PROFILE_MISMATCH")
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        if strategy_version != profile.version:
            raise ValueError("SEMANTIC_BINDING_STRATEGY_VERSION_MISMATCH")
        if len(request.scope.dataset_ids) != 1:
            raise ValueError("SEMANTIC_BINDING_DATASET_SCOPE_REQUIRED")

        self._set_database_timeout(session, timeout_ms)
        strategy_request = request.model_copy(
            update={"strategy_version": SEMANTIC_BINDING_STRATEGY_VERSION}
        )
        provider = self._query_embedding_provider(timeout_ms)
        recall = SemanticBindingHybridRetriever(
            session,
            embedding_provider=provider,
            config=self._hybrid_config,
        ).retrieve(strategy_request)
        policy_result = self._semantic_binding_policy(timeout_ms).apply(recall)
        schema_provider = self._schema_provider or build_semantic_schema_service(
            session
        )
        schema = schema_provider.build_dataset_schema(
            request.tenant_id,
            request.scope.dataset_ids[0],
        )
        bundle = bind_default_time_dimensions(
            strategy_request,
            policy_result.bundle,
            schema,
        )
        payload = bundle_to_semantic_payload(
            strategy_request,
            bundle,
            schema,
        )
        return SemanticBindingExecutionResult(
            bundle=bundle,
            payload=payload,
            filters={
                "plan_fingerprint": recall.plan.fingerprint,
                "subqueries": [
                    {
                        "subquery_id": item.subquery_id,
                        "purpose": item.purpose.value,
                        "required": item.required,
                    }
                    for item in recall.plan.subqueries
                ],
            },
        )

    def _semantic_binding_policy(self, timeout_ms: int) -> SemanticBindingPolicy:
        if self._policy is not None:
            return self._policy
        self._rerank_config.validate()
        if not self._rerank_config.enabled:
            return SemanticBindingPolicy()
        return SemanticBindingPolicy(
            SiliconFlowReranker(
                api_base_url=self._rerank_config.api_base_url,
                api_key=self._rerank_config.api_key,
                model=self._rerank_config.model,
                timeout=min(
                    self._rerank_config.timeout_seconds,
                    max(timeout_ms / 1000, 0.1),
                ),
            )
        )

    def _query_embedding_provider(
        self,
        timeout_ms: int,
    ) -> ObservedEmbeddingProvider | None:
        if not self._hybrid_config.dense_enabled:
            return None
        if self._embedding_startup_error is not None:
            return None
        provider = self._embedding_provider
        if provider is None:
            if self._embedding_config.provider == "openai_compatible":
                provider = OpenAICompatibleEmbeddingProvider(
                    api_base_url=self._embedding_config.api_base_url,
                    api_key=self._embedding_config.api_key,
                    model=self._embedding_config.model,
                    dimension=self._embedding_config.dimension,
                    provider=self._embedding_config.provider,
                    timeout=max(timeout_ms / 1000, 0.1),
                )
            elif self._embedding_config.provider == "sentence_transformers":
                provider = SentenceTransformerEmbeddingProvider(
                    model=self._embedding_config.model,
                    dimension=self._embedding_config.dimension,
                )
            else:
                raise RetrievalConfigurationError(
                    "不支持的语义绑定 embedding provider",
                    details={"reason_code": "EMBEDDING_PROVIDER_UNSUPPORTED"},
                )
        return ObservedEmbeddingProvider(provider, self._hybrid_config.embedding_dimension)

    @staticmethod
    def _set_database_timeout(session: Any, timeout_ms: int) -> None:
        """PostgreSQL 查询使用事务级硬超时，其他测试/适配器保持原行为。"""

        get_bind = getattr(session, "get_bind", None)
        if get_bind is None:
            return
        bind = get_bind()
        if bind.dialect.name != "postgresql":
            return
        session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{timeout_ms}ms"},
        )


__all__ = [
    "SEMANTIC_BINDING_STRATEGY_VERSION",
    "SemanticBindingExecutionResult",
    "SemanticBindingRunner",
]
