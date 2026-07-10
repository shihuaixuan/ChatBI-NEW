"""检索垫片契约测试：入参组装与语义包裁剪。

垫片当前包装 HeadlessKnowledgeAdapter；Step 4 下沉后实现替换，本测试不变，
用于保证签名与语义包结构是稳定契约。
"""

from unittest.mock import patch

from apps.chatbi_capabilities.semantic.retrieval import _to_semantic_package, retrieve_semantic_assets


def test_shim_builds_minimal_graph_context_and_passes_intent():
    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, request):
            captured.update(request)
            return {"hit": False, "status": "missed"}

    with (
        patch("apps.chatbi_workflow.capabilities.adapters.knowledge.HeadlessKnowledgeAdapter", FakeAdapter),
        patch("apps.headless.service.HeadlessSchemaBuilder"),
    ):
        package = retrieve_semantic_assets(
            session=None,
            oid=7,
            dataset_id=3,
            question="上月销售额",
            intent={"metric_mentions": ["销售额"]},
        )

    assert captured["request"] == {"question": "上月销售额", "dataset_id": 3, "tenant_id": 7}
    assert captured["variables"]["intent"] == {"metric_mentions": ["销售额"]}
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
