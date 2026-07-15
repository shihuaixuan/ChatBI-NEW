"""混合召回快速路径、RRF 和错误边界测试。"""

from __future__ import annotations

import pytest

from apps.retrieval.hybrid import (
    HybridRetrievalConfig,
    RecallCandidate,
    SemanticBindingHybridRetriever,
    reciprocal_rank_fusion,
)
from apps.retrieval.schemas import (
    RetrievalChannel,
    RetrievalChannelStatus,
    RetrievalHit,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalSourceType,
)


def _hit(resource_id: str, unit_id: str, title: str) -> RetrievalHit:
    return RetrievalHit(
        resource_id=resource_id,
        resource_type=RetrievalResourceType.METRIC,
        source_type=RetrievalSourceType.HEADLESS,
        source_id="1",
        source_resource_id=f"METRIC:{resource_id}",
        unit_id=unit_id,
        content_kind="identity",
        title=title,
        source_version="schema-1",
        provenance={"index_generation": "generation-1"},
    )


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id="hybrid-1",
        tenant_id=1,
        actor_id=2,
        original_question="GMV",
        rewritten_question="GMV",
        intent=RetrievalIntent(intent_type="metric_query", metric_mentions=["GMV"]),
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )


def test_rrf_folds_units_by_resource_and_keeps_channel_scores_and_ranks():
    first = _hit("100", "1001", "销售额")
    second_unit = _hit("100", "1002", "销售额")
    other = _hit("101", "1011", "订单量")

    hits = reciprocal_rank_fusion(
        {
            RetrievalChannel.EXACT: [RecallCandidate(RetrievalChannel.EXACT, 1.0, first)],
            RetrievalChannel.LEXICAL: [
                RecallCandidate(RetrievalChannel.LEXICAL, 0.8, second_unit),
                RecallCandidate(RetrievalChannel.LEXICAL, 0.7, other),
            ],
            RetrievalChannel.DENSE: [RecallCandidate(RetrievalChannel.DENSE, 0.9, other)],
        },
        rrf_k=60,
        limit=5,
    )

    assert [hit.resource_id for hit in hits] == ["100", "101"]
    assert hits[0].unit_id == "1001"
    assert hits[0].scores.exact == 1.0
    assert hits[0].scores.lexical == 0.8
    assert hits[0].ranks_by_channel == {
        RetrievalChannel.EXACT: 1,
        RetrievalChannel.LEXICAL: 1,
    }
    assert hits[1].ranks_by_channel[RetrievalChannel.DENSE] == 1


class _FastPathStore:
    def __init__(self) -> None:
        self.lexical_called = False
        self.dense_called = False

    def search_exact(self, _request, _subquery, _limit):
        return []

    def search_alias(self, _request, _subquery, _limit):
        return [RecallCandidate(RetrievalChannel.ALIAS, 1.0, _hit("100", "1001", "销售额"))]

    def search_lexical(self, _request, _subquery, _limit):
        self.lexical_called = True
        return []

    def search_dense(self, _request, _subquery, _vector, _limit):
        self.dense_called = True
        return []


def test_unique_approved_alias_uses_fast_path_without_dense_call():
    store = _FastPathStore()
    retriever = SemanticBindingHybridRetriever(
        session=object(),
        config=HybridRetrievalConfig(dense_enabled=True),
        store=store,
    )

    result = retriever.retrieve(_request())

    assert result.index_generations == ("generation-1",)
    assert result.slots[0].fast_path is True
    assert result.slots[0].hits[0].title == "销售额"
    assert store.lexical_called is False
    assert store.dense_called is False
    statuses = {item.channel: item.status for item in result.slots[0].channels}
    assert statuses[RetrievalChannel.ALIAS] == RetrievalChannelStatus.SUCCEEDED
    assert statuses[RetrievalChannel.DENSE] == RetrievalChannelStatus.SKIPPED


def test_hybrid_retriever_rejects_legacy_strategy_version():
    request = _request().model_copy(update={"strategy_version": "semantic-binding-v1"})

    with pytest.raises(ValueError, match="STRATEGY_VERSION_MISMATCH"):
        SemanticBindingHybridRetriever(
            session=object(),
            config=HybridRetrievalConfig(dense_enabled=False),
            store=_FastPathStore(),
        ).retrieve(request)
