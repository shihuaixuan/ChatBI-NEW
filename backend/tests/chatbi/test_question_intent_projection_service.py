from apps.chatbi.models import QuestionIntentProjectionData
from apps.chatbi.services import QuestionIntentProjectionService


def test_projection_service_normalizes_shape_and_semantic_payloads():
    service = QuestionIntentProjectionService()

    shape = service.normalize_shape(
        {
            "intent_type": "invalid",
            "confidence": 2,
            "required_slot_types": ["metric", "unsupported"],
            "query_shape": "invalid",
            "ambiguous_slots": "intent",
        },
        subject_domain={"status": "selected", "domain_id": 1},
    )
    semantic = service.normalize_semantic(
        {
            "metric_mentions": "销售额",
            "time_mentions": ["最近7天"],
            "time_range": {"raw": "最近7天", "value_status": "provided"},
        }
    )

    assert shape == {
        "intent_type": "unknown",
        "confidence": 1.0,
        "required_slot_types": ["metric"],
        "query_shape": {},
        "subject_domain": {"status": "selected", "domain_id": 1},
        "ambiguous_slots": ["intent"],
        "conflict_slots": [],
    }
    assert semantic["metric_mentions"] == ["销售额"]
    assert semantic["time_range"]["normalized"] == {
        "kind": "relative_range",
        "unit": "day",
        "amount": 7,
        "anchor": "today",
        "include_current": True,
        "timezone": "Asia/Shanghai",
    }


def test_projection_service_centralizes_required_slots_and_confirmed_intent():
    service = QuestionIntentProjectionService()

    result = service.project(
        QuestionIntentProjectionData(
            shape={
                "intent_type": "trend_analysis",
                "confidence": 0.8,
                "required_slot_types": ["metric"],
                "query_shape": {"time_grain": "day"},
                "subject_domain": {"status": "not_required"},
                "ambiguous_slots": ["intent"],
                "conflict_slots": ["analysis_type"],
            },
            semantic={
                "metric_mentions": [],
                "time_mentions": ["本月"],
                "time_range": {"raw": "本月", "value_status": "provided"},
            },
            dimensions={
                "dimension_mentions": ["城市"],
                "dimension_slots": [
                    {
                        "name": "城市",
                        "role": "filter",
                        "value": None,
                        "value_status": "ambiguous",
                    }
                ],
                "residual_filter_mentions": [],
            },
            user_feedback={"intent": "comparison_analysis"},
        )
    )

    assert result.payload["intent_type"] == "comparison_analysis"
    assert result.payload["confidence"] == 0.95
    assert result.payload["required_slot_types"] == ["metric", "time_dimension"]
    assert result.payload["ambiguous_slots"] == ["metric", "filter_value"]
    assert result.payload["conflict_slots"] == []
    assert result.payload["dimension_mentions"] == ["城市"]
    assert result.payload["dimension_slots"][0] == {
        "name": "城市",
        "role": "filter",
        "value": None,
        "value_status": "ambiguous",
    }
