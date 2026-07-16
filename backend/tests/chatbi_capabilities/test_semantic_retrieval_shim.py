"""能力层检索入口契约测试：统一请求组装与语义包裁剪。"""

from types import SimpleNamespace
from unittest.mock import patch

from apps.chatbi_capabilities.semantic.retrieval import (
    _to_semantic_package,
    retrieve_semantic_assets,
)


def test_capability_builds_unified_retrieval_request_and_passes_intent():
    captured = {}

    class FakeService:
        def retrieve(self, request):
            captured["request"] = request
            return SimpleNamespace(payload={"hit": False, "status": "missed"})

    with patch(
        "apps.chatbi_capabilities.semantic.retrieval.build_retrieval_service",
        return_value=FakeService(),
    ):
        package = retrieve_semantic_assets(
            session=None,
            oid=7,
            dataset_id=3,
            question="上月销售额",
            intent={"metric_mentions": ["销售额"]},
        )

    request = captured["request"]
    assert request.tenant_id == 7
    assert request.scope.dataset_ids == [3]
    assert request.rewritten_question == "上月销售额"
    assert request.intent.metric_mentions == ["销售额"]
    assert package["hit"] is False
    assert package["status"] == "missed"


def test_semantic_package_trims_candidates_and_reports_truncation():
    raw = {
        "hit": True,
        "status": "hit",
        "dataset_id": 3,
        "tables": ["t"],
        "metrics": ["gmv"],
        "dimensions": [],
        "terms": [],
        "selected_assets": {"metrics": [{"biz_name": "gmv"}]},
        "slot_bindings": {
            "time_filters": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 9,
                    "value": {"kind": "single_date", "anchor": "today"},
                }
            ]
        },
        "candidate_groups": {
            "metrics": [
                {"biz_name": f"m{i}", "score": 1 - i * 0.1, "asset_type": "METRIC", "internal_field": "x"}
                for i in range(8)
            ]
        },
        "ambiguities": [],
        "decision": {"status": "accepted"},
    }

    package = _to_semantic_package(raw, max_per_group=5)

    assert len(package["candidate_groups"]["metrics"]) == 5
    assert package["truncated"] == {"metrics": 3}
    # 只保留公开字段
    assert "internal_field" not in package["candidate_groups"]["metrics"][0]
    assert package["candidate_groups"]["metrics"][0]["biz_name"] == "m0"
    assert package["slot_bindings"]["time_filters"][0]["asset_id"] == 9
