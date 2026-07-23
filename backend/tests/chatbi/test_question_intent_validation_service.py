from apps.chatbi.services.understanding.graph_contracts import (
    intent_retry_feedback,
    validate_intent,
)


def test_intent_validation_returns_complete_valid_contract():
    result = validate_intent(
        {
            "intent_type": "metric_query",
            "metric_mentions": ["访问人数"],
            "dimension_slots": [],
            "time_range": {},
            "ambiguous_slots": [],
            "conflict_slots": [],
            "subject_domain": {"status": "not_required"},
        },
        retry_count=1,
    )

    assert result == {
        "status": "valid",
        "reason_code": "INTENT_VALID",
        "repair_hint": None,
        "retryable": False,
        "retry_count": 1,
        "max_retry_count": 2,
        "violations": [],
        "clarification_required": False,
        "slot_issues": [],
    }


def test_intent_validation_projects_dimension_time_violation_and_retry_limit():
    intent = {
        "intent_type": "metric_query",
        "metric_mentions": ["访问人数"],
        "dimension_slots": [
            {
                "name": "店铺",
                "role": "filter",
                "value": "最近7天",
                "value_status": "provided",
            }
        ],
    }

    first_result = validate_intent(intent, max_retry_count=2)
    final_result = validate_intent(intent, retry_count=1, max_retry_count=2)

    assert first_result["status"] == "invalid"
    assert first_result["reason_code"] == "DIMENSION_VALUE_IS_TIME_EXPRESSION"
    assert first_result["retryable"] is True
    assert first_result["retry_count"] == 1
    assert first_result["max_retry_count"] == 2
    assert first_result["clarification_required"] is True
    assert first_result["violations"] == [
        {
            "slot": "dimension_slots[0].value",
            "dimension": "店铺",
            "value": "最近7天",
        }
    ]
    assert first_result["slot_issues"] == [
        {
            "slot_type": "dimension_value",
            "dimension": "店铺",
            "role": "ambiguous",
            "value_status": "not_provided",
            "reason": "维度值被识别成时间表达，需要用户确认维度值或分组方式",
        }
    ]
    assert final_result["retryable"] is False
    assert final_result["retry_count"] == 2
    assert final_result["violations"] == first_result["violations"]
    assert final_result["slot_issues"] == first_result["slot_issues"]


def test_intent_validation_retry_feedback_keeps_stable_contract():
    feedback = intent_retry_feedback(
        {
            "reason_code": "DIMENSION_VALUE_IS_TIME_EXPRESSION",
            "repair_hint": "请修复维度值",
            "violations": [{"dimension": "店铺"}],
            "retry_count": 1,
        }
    )

    assert feedback == {
        "intent_validation": {
            "reason_code": "DIMENSION_VALUE_IS_TIME_EXPRESSION",
            "repair_hint": "请修复维度值",
            "violations": [{"dimension": "店铺"}],
            "retry_count": 1,
        }
    }
