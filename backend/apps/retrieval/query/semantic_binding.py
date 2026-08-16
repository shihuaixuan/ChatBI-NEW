"""semantic-binding 的独立执行入口。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from apps.retrieval.embedding import (
    EmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.models.dto import (
    RetrievalBundle,
    RetrievalHit,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScores,
    RetrievalSourceType,
)
from apps.retrieval.models.orm import RetrievalQueryTraceModel
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
from apps.retrieval.query.sql_example_query import (
    EXEMPLAR_SOURCE_KEY_TEMPLATE,
    SQLExampleSearchStore,
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
        exemplar_context_enabled: bool | None = None,
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
        self._exemplar_context_enabled = (
            settings.CHATBI_EXEMPLAR_CONTEXT_ENABLED
            if exemplar_context_enabled is None
            else exemplar_context_enabled
        )

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

        started = perf_counter()
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
        bundle = self._attach_verified_exemplars(session, strategy_request, bundle)
        payload = bundle_to_semantic_payload(
            strategy_request,
            bundle,
            schema,
        )
        self._persist_query_trace(
            session,
            strategy_request,
            recall,
            bundle,
            elapsed_ms=(perf_counter() - started) * 1000,
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

    @staticmethod
    def _persist_query_trace(
        session: Any,
        request: RetrievalRequest,
        recall: Any,
        bundle: RetrievalBundle,
        *,
        elapsed_ms: float,
    ) -> None:
        """把可复现的检索诊断写入既有 retrieval_query_trace 表。"""

        if not settings.RETRIEVAL_QUERY_TRACE_ENABLED:
            return
        add = getattr(session, "add", None)
        if not callable(add):
            # 纯内存测试适配器没有持久化端口，不伪造一条无法落库的记录。
            return
        exec_query = getattr(session, "exec", None)
        if callable(exec_query):
            existing = exec_query(
                select(RetrievalQueryTraceModel).where(
                    RetrievalQueryTraceModel.tenant_id == request.tenant_id,
                    RetrievalQueryTraceModel.request_id == request.request_id,
                    RetrievalQueryTraceModel.profile == request.profiles[0].value,
                )
            ).first()
            if existing is not None:
                # 同一请求可能因重试或页面重放再次进入，保持 trace 幂等。
                return
        candidate_ranks = {
            slot.subquery.subquery_id: [
                {
                    "resource_id": hit.resource_id,
                    "ranks_by_channel": {
                        str(channel.value): rank
                        for channel, rank in hit.ranks_by_channel.items()
                    },
                }
                for hit in slot.hits
            ]
            for slot in recall.slots
        }
        query_hash = hashlib.sha256(
            json.dumps(
                {
                    "original_question": request.original_question,
                    "rewritten_question": request.rewritten_question,
                    "intent": request.intent.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        trace = RetrievalQueryTraceModel(
            tenant_id=request.tenant_id,
            actor_id=request.actor_id,
            request_id=request.request_id,
            profile=request.profiles[0].value,
            query_hash=query_hash,
            permission_version=request.scope.permission_version,
            scope_filters=request.scope.model_dump(mode="json"),
            filters={
                "subqueries": [
                    item.model_dump(mode="json") for item in recall.plan.subqueries
                ]
            },
            channels=[item.model_dump(mode="json") for item in bundle.diagnostics.channels],
            candidate_ranks=candidate_ranks,
            decision=bundle.decision.model_dump(mode="json"),
            strategy_version=bundle.diagnostics.strategy_version,
            index_generation=bundle.diagnostics.index_generation,
            latency_ms=max(int(round(elapsed_ms)), 0),
            error_code=bundle.diagnostics.degraded_reason,
            created_at=datetime.now(timezone.utc),
        )
        add(trace)
        flush = getattr(session, "flush", None)
        if callable(flush):
            flush()

    def _attach_verified_exemplars(
        self,
        session: Any,
        request: RetrievalRequest,
        bundle: RetrievalBundle,
    ) -> RetrievalBundle:
        """相似 verified SQL 示例进语义包上下文；关闭开关时明确跳过。"""

        if not self._exemplar_context_enabled:
            return bundle
        exemplar_profile = get_retrieval_profile(RetrievalProfileName.SQL_EXEMPLAR)
        payloads = SQLExampleSearchStore(session).search_exemplar_hits_by_dataset(
            request.tenant_id,
            request.rewritten_question,
            dataset_id=request.scope.dataset_ids[0],
            limit=exemplar_profile.result_limit,
        )
        if not payloads:
            return bundle
        source_key = EXEMPLAR_SOURCE_KEY_TEMPLATE.format(
            workspace_id=request.tenant_id
        )
        hits = [
            RetrievalHit(
                resource_id=f"sql_example:{payload.example_id}",
                resource_type=RetrievalResourceType.SQL_EXEMPLAR,
                source_type=RetrievalSourceType.SQL_EXEMPLAR,
                source_id=source_key,
                source_resource_id=str(payload.example_id),
                unit_id=f"sql-example:{payload.example_id}",
                content_kind="sql_exemplar",
                title=payload.question,
                # SQL 示例正文可能包含裸 SQL；标准语义包只暴露问题和计划摘要。
                snippet="",
                scores=RetrievalScores(final=payload.score),
                metadata={
                    "verification_status": payload.metadata.get(
                        "verification_status", "VERIFIED"
                    ),
                    "semantic_plan_summary": payload.metadata.get(
                        "semantic_plan_summary"
                    ),
                    "plan_fingerprint": payload.metadata.get("plan_fingerprint"),
                    "dataset_id": payload.metadata.get("dataset_id"),
                },
                provenance={"index_generation": payload.index_generation},
                source_version=payload.index_generation,
            )
            for payload in payloads
        ]
        return bundle.model_copy(update={"exemplars": hits})

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
