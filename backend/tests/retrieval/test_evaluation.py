"""统一检索 Gold Set 评测器测试。"""

from datetime import datetime
from pathlib import Path

from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalChannelStatus,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalSlotDecision,
    RetrievalSourceType,
)
from apps.retrieval.projection.payload import semantic_payload_to_bundle
from apps.retrieval.query.evaluation import (
    GoldenSlotExpectation,
    RecordedRetrievalResult,
    RetrievalBaseline,
    RetrievalGoldenCase,
    evaluate_baseline,
    load_gold_set,
)

GOLD_SET_PATH = Path(__file__).parent / "golden" / "semantic_binding.json"


def _asset(asset_id: int) -> AssetReference:
    return AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=asset_id,
        model_id=10,
    )


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id="case-1",
        tenant_id=1,
        actor_id=1,
        metric_phrases=["销售额"],
        dimension_phrases=[],
        scope=RetrievalScope(dataset_ids=[3]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )


def _hit(asset_id: int) -> RetrievalHit:
    return RetrievalHit(
        resource_id=f"headless:metric:{asset_id}",
        resource_type=RetrievalResourceType.METRIC,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id="headless:dataset:3",
        source_resource_id=str(asset_id),
        unit_id=f"headless:metric:{asset_id}:identity",
        content_kind="identity",
        title=f"metric-{asset_id}",
        source_version="1",
        asset_ref=_asset(asset_id),
    )


def _case(expected_status: RetrievalDecisionStatus = RetrievalDecisionStatus.RESOLVED) -> RetrievalGoldenCase:
    expected_selected = [_asset(7)] if expected_status == RetrievalDecisionStatus.RESOLVED else []
    expected_allowed = (
        [
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=7,
                model_id=10,
            )
        ]
        if expected_status == RetrievalDecisionStatus.RESOLVED
        else []
    )
    return RetrievalGoldenCase(
        case_id="case-1",
        request=_request(),
        expected_status=expected_status,
        expected_allowed_assets=expected_allowed,
        slots=[
            GoldenSlotExpectation(
                subquery_id="metric:1",
                purpose=RetrievalPurpose.METRIC,
                expected_status=expected_status,
                relevant_assets=[_asset(7), _asset(8)],
                expected_selected_assets=expected_selected,
                forbidden_assets=[_asset(99)],
            )
        ],
    )


def _bundle(status: RetrievalDecisionStatus = RetrievalDecisionStatus.RESOLVED) -> RetrievalBundle:
    selected = [_asset(7)] if status == RetrievalDecisionStatus.RESOLVED else []
    allowed = (
        [
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=7,
                model_id=10,
            )
        ]
        if status == RetrievalDecisionStatus.RESOLVED
        else []
    )
    return RetrievalBundle(
        request_id="case-1",
        bindings=RetrievalBindings(metrics=[_hit(7), _hit(8)]),
        decision=RetrievalDecision(
            status=status,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:1",
                    purpose=RetrievalPurpose.METRIC,
                    status=status,
                    candidate_assets=[_asset(7), _asset(8)],
                    selected_assets=selected,
                )
            ],
            allowed_asset_ids=allowed,
        ),
        diagnostics=RetrievalDiagnostics(
            strategy_version="semantic-binding",
            index_generation="test-1",
            total_latency_ms=20,
        ),
    )


def _baseline(bundle: RetrievalBundle) -> RetrievalBaseline:
    return RetrievalBaseline(
        implementation="test",
        captured_at=datetime(2026, 7, 14),
        strategy_version="semantic-binding",
        results=[RecordedRetrievalResult(case_id="case-1", bundle=bundle)],
    )


def test_evaluator_reports_perfect_slot_metrics_for_matching_result():
    report = evaluate_baseline([_case()], _baseline(_bundle()), top_k=5)

    assert report.status_accuracy == 1
    assert report.slot_status_accuracy == 1
    assert report.precision_at_1 == 1
    assert report.recall_at_k == 1
    assert report.selected_assets_accuracy == 1
    assert report.allowed_assets_accuracy == 1
    assert report.wrong_auto_resolved_rate == 0
    assert report.security_violation_count == 0
    assert report.channel_status_counts == {}
    assert report.latency_p95_ms == 20


def test_evaluator_detects_wrong_auto_resolution():
    report = evaluate_baseline(
        [_case(RetrievalDecisionStatus.AMBIGUOUS)],
        _baseline(_bundle(RetrievalDecisionStatus.RESOLVED)),
    )

    assert report.status_accuracy == 0
    assert report.wrong_auto_resolved_rate == 1
    assert report.selected_assets_accuracy == 0
    assert report.allowed_assets_accuracy == 0


def test_evaluator_keeps_missing_results_visible():
    baseline = RetrievalBaseline(
        implementation="empty",
        captured_at=datetime(2026, 7, 14),
        strategy_version="semantic-binding",
        results=[],
    )

    report = evaluate_baseline([_case()], baseline)

    assert report.evaluated_case_count == 0
    assert report.missing_result_count == 1
    assert report.cases[0].errors == ["BASELINE_RESULT_MISSING"]


def test_real_semantic_binding_gold_set_is_valid_and_covers_core_states():
    cases = load_gold_set(GOLD_SET_PATH)

    assert len(cases) >= 8
    assert {case.expected_status for case in cases} >= {
        RetrievalDecisionStatus.RESOLVED,
        RetrievalDecisionStatus.AMBIGUOUS,
        RetrievalDecisionStatus.MISSED,
        RetrievalDecisionStatus.CROSS_MODEL,
    }
    assert len({case.case_id for case in cases}) == len(cases)


def test_payload_converter_does_not_assign_all_selected_metrics_to_each_slot():
    request = _request().model_copy(
        update={
            "rewrite_question": "客户GMV和客户订单数",
            "metric_phrases": ["客户GMV", "客户订单数"],
            "intent": RetrievalIntent(
                intent_type="metric_query",
            ),
        }
    )
    raw = {
        "status": "hit",
        "candidate_groups": {
            "metrics": [
                {
                    "asset_type": "METRIC",
                    "asset_id": 7,
                    "model_id": 10,
                    "name": "客户当日GMV",
                    "biz_name": "customer_gmv",
                    "payload": {"alias": ["客户GMV"]},
                    "matched_text": "客户GMV和客户订单数",
                    "score": 1,
                },
                {
                    "asset_type": "METRIC",
                    "asset_id": 8,
                    "model_id": 10,
                    "name": "客户当日订单数",
                    "biz_name": "customer_order_cnt",
                    "payload": {"alias": ["客户订单数"]},
                    "matched_text": "客户GMV和客户订单数",
                    "score": 1,
                },
            ]
        },
        "selected_assets": {
            "metrics": [
                {
                    "asset_type": "METRIC",
                    "asset_id": 7,
                    "model_id": 10,
                    "name": "客户当日GMV",
                    "biz_name": "customer_gmv",
                    "payload": {"alias": ["客户GMV"]},
                    "matched_text": "客户GMV和客户订单数",
                },
                {
                    "asset_type": "METRIC",
                    "asset_id": 8,
                    "model_id": 10,
                    "name": "客户当日订单数",
                    "biz_name": "customer_order_cnt",
                    "payload": {"alias": ["客户订单数"]},
                    "matched_text": "客户GMV和客户订单数",
                },
            ]
        },
    }

    bundle = semantic_payload_to_bundle(
        request,
        raw,
        dense_status=RetrievalChannelStatus.SKIPPED,
    )

    slots = {item.subquery_id: item for item in bundle.decision.slot_decisions}
    assert [item.asset_id for item in slots["metric:1"].selected_assets] == [7]
    assert [item.asset_id for item in slots["metric:2"].selected_assets] == [8]
