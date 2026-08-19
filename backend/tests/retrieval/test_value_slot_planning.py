"""候选资产检索阶段不读取维度值的契约测试。"""

from apps.retrieval.models.dto import (
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.projection.planner import SemanticBindingQueryPlanner


def test_candidate_planner_does_not_create_value_queries_from_user_values():
    request = RetrievalRequest(
        request_id="candidate-only-1",
        tenant_id=1,
        actor_id=2,
        metric_phrases=["销售额"],
        dimension_phrases=["城市"],
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )

    plan = SemanticBindingQueryPlanner().plan(request)

    assert [item.purpose for item in plan.subqueries] == [
        RetrievalPurpose.METRIC,
        RetrievalPurpose.DIMENSION,
    ]
    assert all(item.purpose != RetrievalPurpose.VALUE for item in plan.subqueries)
