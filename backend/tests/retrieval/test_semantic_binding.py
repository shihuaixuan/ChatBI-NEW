"""semantic-binding 执行入口组装测试。"""

from types import SimpleNamespace

import httpx
import pytest

from apps.retrieval.errors import (
    RetrievalConfigurationError,
    RetrievalProviderUnavailableError,
)
from apps.retrieval.models.dto import (
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
from apps.retrieval.projection.payload import bundle_to_semantic_payload
from apps.retrieval.query.policy import RerankCandidate
from apps.retrieval.query.semantic_binding import (
    SEMANTIC_BINDING_STRATEGY_VERSION,
    SemanticBindingRunner,
)
from apps.retrieval.query.semantic_runtime import (
    RetrievalEmbeddingRuntimeConfig,
    RetrievalRerankRuntimeConfig,
)
from apps.retrieval.query.sql_example_query import SQLExemplarHitPayload
from apps.retrieval.reranking import SiliconFlowReranker
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

    class _SchemaBuilder:
        def build_dataset_schema(self, tenant_id, dataset_id):
            observed["schema_scope"] = (tenant_id, dataset_id)
            return _schema()

    monkeypatch.setattr(
        "apps.retrieval.query.semantic_binding.SemanticBindingHybridRetriever",
        _HybridRetriever,
    )
    monkeypatch.setattr(
        "apps.retrieval.query.semantic_binding.build_semantic_schema_service",
        lambda session: (
            observed.__setitem__("schema_session", session) or _SchemaBuilder()
        ),
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
    runner = SemanticBindingRunner(
        embedding_config=config,
        policy=_Policy(),
        exemplar_context_enabled=False,
    )
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


def test_verified_exemplar_enters_bundle_and_semantic_payload_without_raw_sql(monkeypatch):
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

    class _ExemplarStore:
        def __init__(self, session):
            assert session is not None

        def search_exemplar_hits_by_dataset(self, *args, **kwargs):
            _ = args, kwargs
            return [
                SQLExemplarHitPayload(
                    example_id=88,
                    question="本月销售额",
                    metadata={
                        "dataset_id": 20,
                        "verification_status": "VERIFIED",
                        "plan_fingerprint": "plan-88",
                        "semantic_plan_summary": {
                            "metric_ids": [100],
                            "dimension_ids": [],
                        },
                        "sql": "SELECT secret FROM physical_table",
                    },
                    score=0.91,
                    index_generation="generation-example",
                )
            ]

    monkeypatch.setattr(
        "apps.retrieval.query.semantic_binding.SQLExampleSearchStore",
        _ExemplarStore,
    )
    runner = SemanticBindingRunner(exemplar_context_enabled=True)

    attached = runner._attach_verified_exemplars(object(), request, bundle)
    payload = bundle_to_semantic_payload(request, attached, _schema())

    assert len(attached.exemplars) == 1
    assert attached.exemplars[0].snippet == ""
    assert payload["examples"] == [
        {
            "question": "本月销售额",
            "semantic_plan": {"metric_ids": [100], "dimension_ids": []},
            "plan_fingerprint": "plan-88",
            "score": 0.91,
        }
    ]
    assert "sql" not in payload["examples"][0]


def test_exemplar_context_switch_skips_store(monkeypatch):
    class _UnexpectedStore:
        def __init__(self, session):
            raise AssertionError(f"关闭开关后不应访问 exemplar store: {session}")

    monkeypatch.setattr(
        "apps.retrieval.query.semantic_binding.SQLExampleSearchStore",
        _UnexpectedStore,
    )
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

    attached = SemanticBindingRunner(
        exemplar_context_enabled=False
    )._attach_verified_exemplars(object(), request, bundle)

    assert attached is bundle


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


def test_retrieval_rerank_config_uses_shared_siliconflow_key():
    runtime_settings = SimpleNamespace(
        RETRIEVAL_RERANK_ENABLED=True,
        RETRIEVAL_RERANK_API_BASE_URL="https://api.siliconflow.cn/v1",
        RETRIEVAL_RERANK_API_KEY="",
        SILICONFLOW_API_KEY="shared-key",
        RETRIEVAL_EMBEDDING_API_KEY="embedding-key",
        RETRIEVAL_RERANK_MODEL="BAAI/bge-reranker-v2-m3",
        RETRIEVAL_RERANK_TIMEOUT_SECONDS=30.0,
    )

    config = RetrievalRerankRuntimeConfig.from_settings(runtime_settings)

    assert config.api_key == "shared-key"
    config.validate()


def test_retrieval_rerank_config_requires_api_key_when_enabled():
    config = RetrievalRerankRuntimeConfig(
        enabled=True,
        api_base_url="https://api.siliconflow.cn/v1",
        api_key="",
        model="BAAI/bge-reranker-v2-m3",
        timeout_seconds=30.0,
    )

    with pytest.raises(RetrievalConfigurationError) as exc_info:
        config.validate()

    assert exc_info.value.details["reason_code"] == "RERANKER_API_KEY_MISSING"


def test_siliconflow_reranker_maps_scores_back_to_candidate_ids(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.42},
                ]
            }

    def _post(url, *, headers, json, timeout):
        captured.update(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return _Response()

    monkeypatch.setattr(httpx, "post", _post)
    provider = SiliconFlowReranker(
        api_base_url="https://api.siliconflow.cn/v1/",
        api_key="secret",
        model="BAAI/bge-reranker-v2-m3",
        timeout=3.0,
    )

    scores = provider.rerank(
        "销售额",
        (
            RerankCandidate(candidate_id="metric-1", title="销售商品件数"),
            RerankCandidate(candidate_id="metric-2", title="销售订单数"),
        ),
    )

    assert [(item.candidate_id, item.score) for item in scores] == [
        ("metric-2", 0.91),
        ("metric-1", 0.42),
    ]
    assert captured["url"] == "https://api.siliconflow.cn/v1/rerank"
    assert captured["json"] == {
        "model": "BAAI/bge-reranker-v2-m3",
        "query": "销售额",
        "documents": ["销售商品件数", "销售订单数"],
        "return_documents": False,
        "top_n": 2,
    }
    assert captured["timeout"] == 3.0
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_siliconflow_reranker_rejects_incomplete_response(monkeypatch):
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"results": [{"index": 0, "relevance_score": 0.8}]},
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    provider = SiliconFlowReranker(
        api_base_url="https://api.siliconflow.cn/v1",
        api_key="secret",
        model="BAAI/bge-reranker-v2-m3",
        timeout=3.0,
    )

    with pytest.raises(RetrievalProviderUnavailableError) as exc_info:
        provider.rerank(
            "销售额",
            (
                RerankCandidate(candidate_id="metric-1", title="销售商品件数"),
                RerankCandidate(candidate_id="metric-2", title="销售订单数"),
            ),
        )

    assert exc_info.value.details["reason_code"] == "RERANKER_RESPONSE_INVALID"
