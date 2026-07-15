"""Graph 与 Agent 共用的统一检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

from apps.headless.metric_embedding import (
    EmbeddingProvider,
    default_metric_embedding_provider,
)
from apps.headless.service import HeadlessSchemaBuilder
from apps.retrieval.errors import RetrievalConfigurationError, RetrievalQueryError
from apps.retrieval.headless import (
    DenseChannelTracker,
    MetricEmbeddingRuntimeConfig,
    ObservedEmbeddingProvider,
)
from apps.retrieval.legacy_contract import legacy_semantic_result_to_bundle
from apps.retrieval.legacy_headless import (
    HeadlessDocumentRetriever,
    HeadlessKnowledgeAdapter,
)
from apps.retrieval.profiles import get_retrieval_profile
from apps.retrieval.schemas import (
    RetrievalBundle,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalScope,
)
from common.core.config import settings


@dataclass(frozen=True, slots=True)
class RetrievalServiceResult:
    """同时承载新契约与现有调用方兼容结果。"""

    legacy_payload: dict[str, Any]
    bundle: RetrievalBundle


class RetrievalService:
    """统一执行 Headless 语义检索并生成可观测结果。"""

    def __init__(
        self,
        session: Any,
        *,
        schema_builder: HeadlessSchemaBuilder | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_config: MetricEmbeddingRuntimeConfig | None = None,
    ) -> None:
        self._session = session
        self._schema_builder = schema_builder or HeadlessSchemaBuilder(session)
        self._embedding_provider = embedding_provider
        self._embedding_config = embedding_config or MetricEmbeddingRuntimeConfig.from_settings(settings)
        self._embedding_startup_error = self._check_embedding_startup_health()

    def retrieve(self, request: RetrievalRequest) -> RetrievalServiceResult:
        """执行第一版 semantic_binding profile，保持旧排序和门控算法不变。"""

        self._validate_request(request)
        dense_tracker = DenseChannelTracker()
        document_retriever = self._build_document_retriever(dense_tracker)
        adapter = HeadlessKnowledgeAdapter(
            schema_builder=self._schema_builder,
            document_retriever=document_retriever,
        )

        started = perf_counter()
        raw = adapter.retrieve_semantic(
            question=request.rewritten_question,
            dataset_id=request.scope.dataset_ids[0],
            tenant_id=request.tenant_id,
            intent=request.intent.model_dump(mode="json"),
        )
        latency_ms = (perf_counter() - started) * 1000
        bundle = legacy_semantic_result_to_bundle(
            request,
            raw,
            dense_status=dense_tracker.status,
            dense_error_code=dense_tracker.error_code,
            dense_latency_ms=dense_tracker.latency_ms,
            latency_ms=latency_ms,
        )
        payload = dict(raw)
        payload["retrieval_strategy_version"] = request.strategy_version
        payload["retrieval_diagnostics"] = bundle.diagnostics.model_dump(mode="json")
        return RetrievalServiceResult(legacy_payload=payload, bundle=bundle)

    def _build_document_retriever(self, tracker: DenseChannelTracker) -> HeadlessDocumentRetriever:
        config = self._embedding_config
        if not config.enabled:
            return HeadlessDocumentRetriever(dense_tracker=tracker)
        if self._embedding_startup_error is not None:
            tracker.record_error(self._embedding_startup_error)
            return HeadlessDocumentRetriever(dense_tracker=tracker)

        provider = self._embedding_provider or default_metric_embedding_provider()
        observed_provider = ObservedEmbeddingProvider(provider, config.dimension)
        return HeadlessDocumentRetriever(
            top_k=config.top_k,
            metric_embedding_session=self._session,
            metric_embedding_provider=observed_provider,
            dense_tracker=tracker,
            allow_lexical_fallback=config.allow_lexical_fallback,
        )

    def _check_embedding_startup_health(self) -> RetrievalConfigurationError | None:
        """在应用组装阶段检查配置与索引会话，查询时复用检查结果。"""

        config = self._embedding_config
        if not config.enabled:
            return None
        try:
            config.validate()
            if self._embedding_provider is None and config.provider != "openai_compatible":
                raise RetrievalConfigurationError(
                    f"不支持的指标 embedding provider: {config.provider}",
                    details={"reason_code": "EMBEDDING_PROVIDER_UNSUPPORTED"},
                )
            if self._session is None:
                raise RetrievalConfigurationError(
                    "指标 embedding 索引会话不可用",
                    details={"reason_code": "EMBEDDING_INDEX_SESSION_MISSING"},
                )
        except RetrievalConfigurationError as exc:
            if not config.allow_lexical_fallback:
                raise
            return exc
        return None

    @staticmethod
    def _validate_request(request: RetrievalRequest) -> None:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise RetrievalQueryError("M1 RetrievalService 仅支持 semantic_binding profile")
        if request.strategy_version != "semantic-binding-v1":
            raise RetrievalQueryError("旧 RetrievalService 仅支持 semantic-binding-v1")
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
    strategy_version: str = "semantic-binding-v1",
) -> RetrievalRequest:
    """把已确认问题理解投影为统一检索请求。"""

    source_intent = intent or {}
    intent_fields = RetrievalIntent.model_fields
    intent_payload = {key: value for key, value in source_intent.items() if key in intent_fields}
    intent_payload.setdefault("intent_type", str(source_intent.get("intent_type") or "metric_query"))
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
        strategy_version=strategy_version,
    )


def build_semantic_binding_shadow_request(
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
) -> RetrievalRequest:
    """构造 P1-4 shadow 请求，不改变现有 Graph/Agent 默认 v1 行为。"""

    profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
    return build_semantic_binding_request(
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        original_question=original_question,
        rewritten_question=rewritten_question,
        intent=intent,
        request_id=request_id,
        principal_roles=principal_roles,
        principal_role_ids=principal_role_ids,
        permission_version=permission_version,
        source_ids=source_ids,
        strategy_version=profile.version,
    )


def build_retrieval_service(
    session: Any,
    *,
    schema_builder: HeadlessSchemaBuilder | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_config: MetricEmbeddingRuntimeConfig | None = None,
) -> RetrievalService:
    """应用组装层共用的 RetrievalService 工厂。"""

    return RetrievalService(
        session,
        schema_builder=schema_builder,
        embedding_provider=embedding_provider,
        embedding_config=embedding_config,
    )
