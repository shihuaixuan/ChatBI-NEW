"""semantic-binding 执行入口组装测试。"""

from types import SimpleNamespace

import pytest

from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.schemas import (
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.semantic_binding import (
    SEMANTIC_BINDING_STRATEGY_VERSION,
    SemanticBindingRunner,
)
from apps.retrieval.semantic_runtime import RetrievalEmbeddingRuntimeConfig
from apps.semantic.models.dto import DatasetSchema, SchemaElement


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id="semantic-binding-runner",
        tenant_id=1,
        actor_id=2,
        original_question="销售额",
        rewritten_question="销售额",
        intent=RetrievalIntent(intent_type="metric_query", metric_mentions=["销售额"]),
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )


def _schema() -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            model=10,
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        )
    )


def test_semantic_binding_connects_recall_policy_schema_and_payload_projection(monkeypatch):
    request = _request()
    bundle = RetrievalBundle(
        request_id=request.request_id,
        bindings=RetrievalBindings(),
        decision=RetrievalDecision(status=RetrievalDecisionStatus.MISSED),
        diagnostics=RetrievalDiagnostics(
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
            index_generation="generation-2",
        ),
    )
    observed = {}

    class _HybridRetriever:
        def __init__(self, session, *, embedding_provider, config):
            observed["session"] = session
            observed["provider"] = embedding_provider
            observed["dense_enabled"] = config.dense_enabled
            observed["dense_error_code"] = config.dense_unavailable_error_code

        def retrieve(self, strategy_request):
            observed["strategy_version"] = strategy_request.strategy_version
            return SimpleNamespace(
                plan=SimpleNamespace(fingerprint="plan-1", subqueries=()),
            )

    class _Policy:
        def apply(self, _recall):
            return SimpleNamespace(bundle=bundle)

    class _SchemaRepository:
        def __init__(self, session):
            observed["schema_session"] = session

    class _SchemaBuilder:
        def __init__(self, repository):
            assert isinstance(repository, _SchemaRepository)

        def build_dataset_schema(self, tenant_id, dataset_id):
            observed["schema_scope"] = (tenant_id, dataset_id)
            return _schema()

    monkeypatch.setattr(
        "apps.retrieval.semantic_binding.SemanticBindingHybridRetriever",
        _HybridRetriever,
    )
    monkeypatch.setattr(
        "apps.retrieval.semantic_binding.SemanticSchemaService",
        _SchemaBuilder,
    )
    monkeypatch.setattr(
        "apps.retrieval.semantic_binding.SemanticSchemaLoader",
        _SchemaRepository,
    )
    config = RetrievalEmbeddingRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        api_base_url="https://embedding.example/v1",
        api_key="",
        model="BAAI/bge-m3",
        dimension=1024,
        top_k=20,
    )
    runner = SemanticBindingRunner(embedding_config=config, policy=_Policy())
    session = object()

    result = runner.run(
        session,
        request,
        SEMANTIC_BINDING_STRATEGY_VERSION,
        timeout_ms=1500,
    )

    assert result.payload["status"] == "missed"
    assert result.filters["plan_fingerprint"] == "plan-1"
    assert observed == {
        "session": session,
        "provider": None,
        "dense_enabled": True,
        "dense_error_code": "EMBEDDING_API_KEY_MISSING",
        "strategy_version": SEMANTIC_BINDING_STRATEGY_VERSION,
        "schema_session": session,
        "schema_scope": (1, 20),
    }


def test_semantic_binding_rejects_invalid_embedding_config_when_fallback_is_disabled():
    config = RetrievalEmbeddingRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        api_base_url="https://embedding.example/v1",
        api_key="",
        model="BAAI/bge-m3",
        dimension=1024,
        top_k=20,
        allow_lexical_fallback=False,
    )

    with pytest.raises(RetrievalConfigurationError) as exc_info:
        SemanticBindingRunner(embedding_config=config)

    assert exc_info.value.details["reason_code"] == "EMBEDDING_API_KEY_MISSING"


def test_sentence_transformer_embedding_config_does_not_require_remote_credentials():
    config = RetrievalEmbeddingRuntimeConfig(
        enabled=True,
        provider="sentence_transformers",
        api_base_url="",
        api_key="",
        model="BAAI/bge-m3",
        dimension=1024,
        top_k=20,
        allow_lexical_fallback=False,
    )

    config.validate()
    runner = SemanticBindingRunner(embedding_config=config)

    assert runner._embedding_startup_error is None


def test_retrieval_embedding_config_reads_unified_setting_names():
    runtime_settings = SimpleNamespace(
        RETRIEVAL_EMBEDDING_ENABLED=True,
        RETRIEVAL_EMBEDDING_PROVIDER="sentence_transformers",
        RETRIEVAL_EMBEDDING_API_BASE_URL="",
        RETRIEVAL_EMBEDDING_API_KEY="",
        RETRIEVAL_EMBEDDING_MODEL="BAAI/bge-m3",
        RETRIEVAL_EMBEDDING_DIMENSION=1024,
        RETRIEVAL_EMBEDDING_TOP_K=20,
        RETRIEVAL_EMBEDDING_ALLOW_LEXICAL_FALLBACK=False,
    )

    config = RetrievalEmbeddingRuntimeConfig.from_settings(runtime_settings)

    assert config == RetrievalEmbeddingRuntimeConfig(
        enabled=True,
        provider="sentence_transformers",
        api_base_url="",
        api_key="",
        model="BAAI/bge-m3",
        dimension=1024,
        top_k=20,
        allow_lexical_fallback=False,
    )
