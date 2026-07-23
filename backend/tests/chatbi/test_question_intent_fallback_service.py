import pytest

from apps.chatbi.services.understanding import QuestionIntentFallbackService


def test_intent_fallback_keeps_empty_and_vague_question_contracts():
    service = QuestionIntentFallbackService()

    assert service.empty_intent() == {
        "intent_type": "unknown",
        "confidence": 0.4,
        "metric_mentions": [],
        "dimension_mentions": [],
        "dimension_slots": [],
        "time_mentions": [],
        "time_range": {"raw": None, "value_status": "not_provided"},
        "filter_mentions": [],
        "required_slot_types": [],
        "query_shape": {},
        "ambiguous_slots": ["metric"],
        "conflict_slots": [],
    }
    vague = service.infer("看一下情况")
    assert vague["intent_type"] == "unknown"
    assert vague["required_slot_types"] == ["metric"]
    assert vague["query_shape"] == {"select_mode": "unknown"}
    assert vague["ambiguous_slots"] == ["metric"]


def test_intent_fallback_extracts_metric_dimension_value_and_time():
    result = QuestionIntentFallbackService().infer("今天1号档口的访问人数")

    assert result["intent_type"] == "metric_query"
    assert result["metric_mentions"] == ["访问人数"]
    assert result["dimension_mentions"] == ["档口"]
    assert result["dimension_slots"] == [
        {
            "name": "档口",
            "role": "filter",
            "value": "1",
            "value_status": "provided",
        }
    ]
    assert result["time_mentions"] == ["今天"]
    assert result["time_range"] == {
        "raw": "今天",
        "value_status": "provided",
    }


def test_intent_fallback_keeps_multiple_metrics_and_trend_shape():
    result = QuestionIntentFallbackService().infer(
        "最近 30 天每天的总订单数和总 GMV 趋势如何？"
    )

    assert result["intent_type"] == "trend_analysis"
    assert result["metric_mentions"] == ["订单数", "GMV"]
    assert result["time_mentions"] == ["近 30 天"]
    assert result["required_slot_types"] == ["metric", "time_dimension"]
    assert result["query_shape"] == {
        "select_mode": "aggregate",
        "needs_group_by": True,
        "time_grain": "day",
    }


def test_intent_fallback_extracts_absolute_month_ranking_and_limit():
    result = QuestionIntentFallbackService().infer(
        "2026 年 6 月销售 GMV 最高的 5 个档口是哪些？"
    )

    assert result["intent_type"] == "ranking_analysis"
    assert result["metric_mentions"] == ["GMV"]
    assert result["dimension_slots"] == [
        {
            "name": "档口",
            "role": "group_by",
            "value": None,
            "value_status": "not_provided",
        }
    ]
    assert result["time_mentions"] == ["2026 年 6 月"]
    assert result["query_shape"] == {
        "select_mode": "aggregate",
        "needs_group_by": True,
        "needs_order_by": True,
        "order_direction": "desc",
        "limit": 5,
    }


@pytest.mark.parametrize(
    ("question", "intent_type", "required_slot_types", "select_mode"),
    [
        ("本月销售额同比如何", "comparison_analysis", ["metric", "comparison_target"], "aggregate"),
        ("各渠道销售额占比", "share_analysis", ["metric", "dimension"], "share"),
        ("本月销售额为什么下降", "anomaly_analysis", ["metric", "time_range"], "diagnostic"),
        ("客户明细列表", "detail_query", ["dimension"], "detail"),
        ("本月销售额", "metric_query", ["metric"], "aggregate"),
    ],
)
def test_intent_fallback_preserves_analysis_type_priority(
    question: str,
    intent_type: str,
    required_slot_types: list[str],
    select_mode: str,
):
    result = QuestionIntentFallbackService().infer(question)

    assert result["intent_type"] == intent_type
    assert result["required_slot_types"] == required_slot_types
    assert result["query_shape"]["select_mode"] == select_mode


def test_intent_fallback_infers_ascending_bottom_limit():
    result = QuestionIntentFallbackService().infer("销售额后3个地区")

    assert result["intent_type"] == "ranking_analysis"
    assert result["query_shape"]["order_direction"] == "asc"
    assert result["query_shape"]["limit"] == 3
