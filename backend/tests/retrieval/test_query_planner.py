"""二期候选资产检索 QueryPlanner 的确定性契约测试。"""

from __future__ import annotations

from apps.retrieval.models.dto import (
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.projection.planner import SemanticBindingQueryPlanner


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id="planner-1",
        tenant_id=1,
        actor_id=2,
        metric_phrases=["销售额", "订单数"],
        dimension_phrases=["城市"],
        scope=RetrievalScope(
            dataset_ids=[20],
            source_ids=["dataset:20"],
            permission_version="permission-3",
        ),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )


def test_planner_only_generates_metric_and_dimension_phrase_queries():
    plan = SemanticBindingQueryPlanner().plan(_request())

    assert [
        (item.subquery_id, item.purpose, item.text, item.role, item.required)
        for item in plan.subqueries
    ] == [
        ("metric:1", RetrievalPurpose.METRIC, "销售额", None, True),
        ("metric:2", RetrievalPurpose.METRIC, "订单数", None, True),
        ("dimension:1", RetrievalPurpose.DIMENSION, "城市", None, True),
    ]
    assert all(item.filters["tenant_id"] == 1 for item in plan.subqueries)
    assert all(item.filters["dataset_ids"] == [20] for item in plan.subqueries)


def test_planner_uses_the_rewrite_model_phrase_without_whole_question_fallback():
    request = _request().model_copy(
        update={"metric_phrases": ["销售订单平均客单价"]}
    )

    plan = SemanticBindingQueryPlanner().plan(request)

    assert plan.subqueries[0].text == "销售订单平均客单价"


def test_planner_fingerprint_is_stable_and_empty_phrases_emit_no_queries():
    planner = SemanticBindingQueryPlanner()
    request = _request()

    assert planner.plan(request).fingerprint == planner.plan(request).fingerprint
    empty_request = request.model_copy(
        update={"metric_phrases": [], "dimension_phrases": []}
    )
    assert planner.plan(empty_request).subqueries == ()


def test_planner_deduplicates_dimension_phrases():
    request = _request().model_copy(update={"dimension_phrases": ["店铺", "店铺"]})

    plan = SemanticBindingQueryPlanner().plan(request)

    dimension_queries = [
        item for item in plan.subqueries if item.purpose == RetrievalPurpose.DIMENSION
    ]
    assert [(item.subquery_id, item.text, item.role) for item in dimension_queries] == [
        ("dimension:1", "店铺", None)
    ]
