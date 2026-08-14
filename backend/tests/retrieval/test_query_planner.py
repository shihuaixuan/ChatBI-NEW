"""语义绑定分槽 QueryPlanner 的确定性契约测试。"""

from __future__ import annotations

from apps.retrieval.models.dto import (
    RetrievalDimensionSlot,
    RetrievalIntent,
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
        original_question="按城市看北京和上海的销售额与订单数",
        rewritten_question="按城市看北京和上海的销售额与订单数",
        intent=RetrievalIntent(
            intent_type="metric_query",
            metric_mentions=["销售额", "订单数", "销售额"],
            dimension_mentions=["城市", "城市"],
            dimension_slots=[
                RetrievalDimensionSlot(
                    name="城市",
                    role="group_by",
                ),
                RetrievalDimensionSlot(
                    name="城市",
                    role="filter",
                    value="北京和上海",
                    value_status="provided",
                ),
            ],
            subject_domain={"terms": ["成交", "成交"]},
        ),
        scope=RetrievalScope(
            dataset_ids=[20],
            source_ids=["dataset:20"],
            permission_version="permission-3",
        ),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )


def test_planner_generates_asset_slots_without_retrieving_dimension_values():
    plan = SemanticBindingQueryPlanner().plan(_request())

    assert [
        (item.subquery_id, item.purpose, item.text, item.role, item.required)
        for item in plan.subqueries
    ] == [
        ("metric:1", RetrievalPurpose.METRIC, "销售额", None, True),
        ("metric:2", RetrievalPurpose.METRIC, "订单数", None, True),
        ("dimension:1", RetrievalPurpose.DIMENSION, "城市", "group_by", True),
        ("dimension:2", RetrievalPurpose.DIMENSION, "城市", "filter", True),
        ("term:1", RetrievalPurpose.TERM, "成交", None, False),
    ]
    assert all(item.filters["tenant_id"] == 1 for item in plan.subqueries)
    assert all(item.filters["dataset_ids"] == [20] for item in plan.subqueries)
    assert all(item.purpose != RetrievalPurpose.VALUE for item in plan.subqueries)


def test_planner_fingerprint_is_stable_and_does_not_use_whole_question_as_metric_fallback():
    planner = SemanticBindingQueryPlanner()
    request = _request()

    assert planner.plan(request).fingerprint == planner.plan(request).fingerprint
    no_mentions = request.model_copy(
        update={
            "intent": request.intent.model_copy(
                update={"metric_mentions": [], "dimension_slots": []}
            )
        }
    )
    assert planner.plan(no_mentions).subqueries[-1].purpose == RetrievalPurpose.TERM
    assert all(
        item.text != request.rewritten_question
        for item in planner.plan(no_mentions).subqueries
    )


def test_planner_deduplicates_same_dimension_role_regardless_of_literal_value():
    request = _request()
    request = request.model_copy(
        update={
            "intent": request.intent.model_copy(
                update={
                    "dimension_slots": [
                        RetrievalDimensionSlot(
                            name="店铺",
                            role="filter",
                            value="100011",
                            value_status="provided",
                        ),
                        RetrievalDimensionSlot(
                            name="店铺",
                            role="filter",
                            value="100012",
                            value_status="provided",
                        ),
                    ]
                }
            )
        }
    )

    plan = SemanticBindingQueryPlanner().plan(request)

    dimension_queries = [
        item for item in plan.subqueries if item.purpose == RetrievalPurpose.DIMENSION
    ]
    assert [(item.subquery_id, item.text, item.role) for item in dimension_queries] == [
        ("dimension:1", "店铺", "filter")
    ]


def test_planner_preserves_detail_display_dimension_role():
    request = _request().model_copy(
        update={
            "intent": _request().intent.model_copy(
                update={
                    "dimension_slots": [
                        RetrievalDimensionSlot(
                            name="是否超时",
                            role="display",
                        )
                    ]
                }
            )
        }
    )

    plan = SemanticBindingQueryPlanner().plan(request)

    dimension_queries = [
        item for item in plan.subqueries if item.purpose == RetrievalPurpose.DIMENSION
    ]
    assert [(item.text, item.role) for item in dimension_queries] == [
        ("是否超时", "display")
    ]
