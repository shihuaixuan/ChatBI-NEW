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


def test_rewrite_projection_accepts_only_the_new_rewrite_contract():
    result = graph_contracts.project_rewrite(
        {
            "original_question": "今日店铺销售额",
            "rewrite_question": "今日店铺销售额",
            "metric_phrases": ["销售额"],
            "dimension_phrases": ["店铺"],
        },
        original_question="今日店铺销售额",
    )

    assert result == {
        "original_question": "今日店铺销售额",
        "rewrite_question": "今日店铺销售额",
        "metric_phrases": ["销售额"],
        "dimension_phrases": ["店铺"],
    }


def test_rewrite_projection_rejects_the_old_contract():
    with pytest.raises(ValidationError):
        graph_contracts.project_rewrite(
            {
                "rewritten_question": "今日店铺销售额",
                "need_user_input": False,
                "missing_slots": [],
                "image_profile_hint": None,
            },
            original_question="今日店铺销售额",
        )
