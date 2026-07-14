"""统一 RetrievalService 的入口一致性与 dense 错误语义测试。"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from sqlalchemy.exc import ProgrammingError

from apps.chatbi_capabilities.semantic.retrieval import retrieve_semantic_assets
from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    HeadlessKnowledgeAdapter,
)
from apps.headless.metric_embedding import StaticEmbeddingProvider
from apps.headless.schemas import DataSetSchema, SchemaElement
from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.headless import MetricEmbeddingRuntimeConfig
from apps.retrieval.schemas import RetrievalChannel, RetrievalChannelStatus
from apps.retrieval.service import RetrievalService, build_semantic_binding_request


def _element(asset_type: str, asset_id: int, name: str, biz_name: str, **kwargs) -> SchemaElement:
    return SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=kwargs.pop("model", 10),
        id=asset_id,
        name=name,
        biz_name=biz_name,
        type=asset_type,
        alias=kwargs.pop("alias", []),
        description=kwargs.pop("description", None),
    )


def _schema() -> DataSetSchema:
    return DataSetSchema(
        data_set=_element("DATASET", 20, "经营分析", "business"),
        metrics=[
            _element(
                "METRIC",
                100,
                "销售额",
                "sales_amount",
                alias=["GMV"],
                description="订单实付金额汇总",
            )
        ],
        dimensions=[_element("DIMENSION", 200, "地区", "region")],
    )


class _SchemaBuilder:
    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        assert oid == 1
        assert dataset_id == 20
        return _schema()


class _EmbeddingSession:
    def execute(self, _statement, _params=None):
        return [(100, 0.91)]


class _MissingIndexSession:
    def execute(self, _statement, _params=None):
        raise ProgrammingError("vector query", {}, Exception("missing vector index"))


class _TimeoutProvider:
    provider = "test"
    model = "timeout"
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        raise httpx.ReadTimeout("provider timeout")


class _UnexpectedFailureProvider:
    provider = "test"
    model = "unexpected"
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        raise ValueError("unexpected provider bug")


def _embedding_config(
    *,
    enabled: bool = True,
    api_key: str = "test-key",
    dimension: int = 2,
    allow_lexical_fallback: bool = True,
) -> MetricEmbeddingRuntimeConfig:
    return MetricEmbeddingRuntimeConfig(
        enabled=enabled,
        provider="test",
        api_base_url="https://embedding.example/v1",
        api_key=api_key,
        model="test-model",
        dimension=dimension,
        top_k=20,
        allow_lexical_fallback=allow_lexical_fallback,
    )


def _request():
    return build_semantic_binding_request(
        request_id="request-1",
        tenant_id=1,
        actor_id=2,
        dataset_id=20,
        original_question="GMV",
        rewritten_question="GMV",
        intent={"intent_type": "metric_query", "metric_mentions": ["GMV"]},
    )


def _dense_diagnostic(result):
    return next(
        item
        for item in result.bundle.diagnostics.channels
        if item.channel == RetrievalChannel.DENSE
    )


def test_embedding_disabled_is_reported_as_skipped():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_config=_embedding_config(enabled=False),
    )

    result = service.retrieve(_request())

    assert _dense_diagnostic(result).status == RetrievalChannelStatus.SKIPPED
    assert result.legacy_payload["status"] == "hit"


def test_missing_embedding_api_key_is_explicitly_unavailable():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_config=_embedding_config(api_key=""),
    )

    result = service.retrieve(_request())
    diagnostic = _dense_diagnostic(result)

    assert diagnostic.status == RetrievalChannelStatus.UNAVAILABLE
    assert diagnostic.error_code == "EMBEDDING_API_KEY_MISSING"
    assert result.bundle.diagnostics.degraded_reason == "EMBEDDING_API_KEY_MISSING"


@pytest.mark.parametrize(
    ("updates", "error_code"),
    [
        ({"provider": ""}, "EMBEDDING_PROVIDER_MISSING"),
        ({"api_base_url": "invalid-url"}, "EMBEDDING_API_URL_INVALID"),
        ({"model": ""}, "EMBEDDING_MODEL_MISSING"),
        ({"dimension": 0}, "EMBEDDING_DIMENSION_INVALID"),
    ],
)
def test_embedding_startup_health_checks_all_required_configuration(updates, error_code):
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_provider=StaticEmbeddingProvider(vector=[1.0, 0.0]),
        embedding_config=replace(_embedding_config(), **updates),
    )

    diagnostic = _dense_diagnostic(service.retrieve(_request()))

    assert diagnostic.status == RetrievalChannelStatus.UNAVAILABLE
    assert diagnostic.error_code == error_code


def test_embedding_provider_timeout_is_failed_and_lexical_result_survives():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_provider=_TimeoutProvider(),
        embedding_config=_embedding_config(),
    )

    result = service.retrieve(_request())
    diagnostic = _dense_diagnostic(result)

    assert diagnostic.status == RetrievalChannelStatus.FAILED
    assert diagnostic.error_code == "EMBEDDING_PROVIDER_TIMEOUT"
    assert result.legacy_payload["selected_assets"]["metrics"][0]["asset_id"] == 100


def test_embedding_dimension_mismatch_is_failed():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_provider=StaticEmbeddingProvider(vector=[1.0]),
        embedding_config=_embedding_config(dimension=2),
    )

    result = service.retrieve(_request())
    diagnostic = _dense_diagnostic(result)

    assert diagnostic.status == RetrievalChannelStatus.FAILED
    assert diagnostic.error_code == "EMBEDDING_DIMENSION_MISMATCH"


def test_missing_embedding_index_is_explicitly_unavailable():
    service = RetrievalService(
        _MissingIndexSession(),
        schema_builder=_SchemaBuilder(),
        embedding_provider=StaticEmbeddingProvider(vector=[1.0, 0.0]),
        embedding_config=_embedding_config(),
    )

    result = service.retrieve(_request())
    diagnostic = _dense_diagnostic(result)

    assert diagnostic.status == RetrievalChannelStatus.UNAVAILABLE
    assert diagnostic.error_code == "EMBEDDING_INDEX_UNAVAILABLE"


def test_unknown_provider_error_is_not_silently_degraded():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_provider=_UnexpectedFailureProvider(),
        embedding_config=_embedding_config(),
    )

    with pytest.raises(ValueError, match="unexpected provider bug"):
        service.retrieve(_request())


def test_configuration_error_raises_when_lexical_fallback_is_disabled():
    with pytest.raises(RetrievalConfigurationError):
        RetrievalService(
            _EmbeddingSession(),
            schema_builder=_SchemaBuilder(),
            embedding_config=_embedding_config(api_key="", allow_lexical_fallback=False),
        )


def test_graph_and_agent_use_the_same_service_decision_and_channel_status():
    service = RetrievalService(
        _EmbeddingSession(),
        schema_builder=_SchemaBuilder(),
        embedding_config=_embedding_config(enabled=False),
    )
    graph = HeadlessKnowledgeAdapter(
        schema_builder=_SchemaBuilder(),
        retrieval_service=service,
    ).retrieve(
        {
            "run_id": "run-1",
            "request": {
                "question": "GMV",
                "dataset_id": 20,
                "tenant_id": 1,
                "user_id": 2,
            },
            "variables": {
                "intent": {"intent_type": "metric_query", "metric_mentions": ["GMV"]}
            },
        }
    )
    agent = retrieve_semantic_assets(
        None,
        oid=1,
        dataset_id=20,
        question="GMV",
        intent={"intent_type": "metric_query", "metric_mentions": ["GMV"]},
        actor_id=2,
        request_id="run-1",
        retrieval_service=service,
    )

    assert graph["status"] == agent["status"]
    assert graph["decision"] == agent["decision"]
    assert [item["asset_id"] for item in graph["candidate_groups"]["metrics"]] == [
        item["asset_id"] for item in agent["candidate_groups"]["metrics"]
    ]
    assert graph["retrieval_strategy_version"] == agent["retrieval_strategy_version"]
    assert graph["retrieval_diagnostics"]["channels"] == agent["retrieval_diagnostics"]["channels"]
