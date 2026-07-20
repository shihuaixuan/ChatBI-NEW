import pytest
from pydantic import ValidationError

from apps.chatbi.services.understanding import graph_contracts


def test_classification_precondition_rejects_empty_question_and_missing_dataset():
    assert graph_contracts.classification_precondition("", dataset_id=3) == {
        "category": "forbidden",
        "reason": "empty_question",
        "risk_level": "medium",
        "confidence": 1.0,
    }
    assert graph_contracts.classification_precondition("今日销售额", dataset_id=None) == {
        "category": "forbidden",
        "reason": "missing_dataset",
        "risk_level": "medium",
        "confidence": 1.0,
    }
    assert graph_contracts.classification_precondition("今日销售额", dataset_id=3) is None


def test_classification_projection_keeps_contract_and_rejects_invalid_category():
    assert graph_contracts.project_classification(
        {
            "category": "data",
            "reason": "用户查询业务指标",
            "risk_level": "low",
            "confidence": 0.92,
        }
    ) == {
        "category": "data",
        "reason": "用户查询业务指标",
        "risk_level": "low",
        "confidence": 0.92,
    }
    with pytest.raises(ValidationError):
        graph_contracts.project_classification(
            {
                "category": "other",
                "reason": "非法分类",
                "risk_level": "low",
                "confidence": 0.9,
            }
        )


def test_rewrite_projection_removes_only_false_missing_dataset_slot():
    result = graph_contracts.project_rewrite(
        {
            "rewritten_question": "今日店铺销售额",
            "need_user_input": True,
            "missing_slots": ["dataset_id", "metric"],
            "image_profile_hint": "table",
        },
        dataset_id=3,
    )

    assert result == {
        "rewritten_question": "今日店铺销售额",
        "need_user_input": True,
        "missing_slots": ["metric"],
        "image_profile_hint": "table",
    }


def test_rewrite_projection_clears_clarification_when_dataset_was_only_missing_slot():
    result = graph_contracts.project_rewrite(
        {
            "rewritten_question": "今日店铺销售额",
            "need_user_input": True,
            "missing_slots": ["dataset_id"],
            "image_profile_hint": None,
        },
        dataset_id=3,
    )

    assert result["need_user_input"] is False
    assert result["missing_slots"] == []


def test_rewrite_fallback_keeps_existing_graph_clarification_rules():
    assert graph_contracts.empty_rewrite() == {
        "rewritten_question": "",
        "need_user_input": True,
        "missing_slots": ["question"],
        "image_profile_hint": None,
    }
    assert graph_contracts.fallback_rewrite("需要澄清的问题", {}) == {
        "rewritten_question": "需要澄清的问题",
        "need_user_input": True,
        "missing_slots": ["metric"],
        "image_profile_hint": None,
    }
    assert graph_contracts.fallback_rewrite(
        "需要澄清的问题",
        {"metric": "销售额"},
    ) == {
        "rewritten_question": "需要澄清的问题",
        "need_user_input": False,
        "missing_slots": [],
        "image_profile_hint": None,
    }
