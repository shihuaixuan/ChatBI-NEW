import pytest
from pydantic import ValidationError

from apps.chatbi.models.dto.question_understanding import (
    DimensionRecognitionOutput,
    IntentRecognitionOutput,
    NaturalLanguageIntentOutputBase,
    QuestionRewriteOutput,
    QuestionRewriteOutputBase,
)
from apps.chatbi.orchestration.graph.schemas.v1 import (
    IntentRecognitionOutput as GraphIntentRecognitionOutput,
)
from apps.chatbi.orchestration.graph.schemas.v1 import (
    QuestionRewriteOutput as GraphQuestionRewriteOutput,
)


def test_agent_intent_output_keeps_strict_dimension_and_time_types():
    intent = IntentRecognitionOutput.model_validate(
        {
            "intent_type": "metric_query",
            "confidence": 0.95,
            "metric_mentions": ["销售额"],
            "dimension_slots": [
                {
                    "name": "城市",
                    "role": "group_by",
                    "value": None,
                    "value_status": "not_provided",
                }
            ],
            "time_range": {"raw": "本月", "value_status": "provided"},
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
            },
        }
    )

    assert intent.dimension_slots[0].name == "城市"
    assert intent.time_range.raw == "本月"
    comparison = IntentRecognitionOutput.model_validate(
        {
            "intent_type": "comparison_analysis",
            "confidence": 0.95,
            "metric_mentions": ["GMV"],
            "dimension_mentions": ["店铺"],
            "dimension_slots": [
                {
                    "name": "店铺",
                    "role": "filter",
                    "value": ["100011", "100012"],
                    "value_status": "provided",
                }
            ],
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
            },
        }
    )
    assert comparison.dimension_slots[0].value == ["100011", "100012"]
    with pytest.raises(ValidationError):
        IntentRecognitionOutput.model_validate(
            {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "dimension_slots": [{"name": "城市", "role": "unsupported"}],
                "query_shape": {"select_mode": "aggregate"},
            }
        )


def test_agent_query_shape_rejects_unknown_fields_and_invalid_limit_type():
    base = {
        "intent_type": "ranking_analysis",
        "confidence": 0.95,
        "metric_mentions": ["销售额"],
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        IntentRecognitionOutput.model_validate(
            {
                **base,
                "query_shape": {
                    "select_mode": "aggregate",
                    "needs_order_by": True,
                    "order_direction": "desc",
                    "limit": 5,
                    "sql_order": "sales desc",
                },
            }
        )
    with pytest.raises(ValidationError, match="int_type"):
        IntentRecognitionOutput.model_validate(
            {
                **base,
                "query_shape": {
                    "select_mode": "aggregate",
                    "needs_order_by": True,
                    "order_direction": "desc",
                    "limit": "5",
                },
            }
        )


def test_graph_outputs_inherit_shared_bases_without_expanding_payload():
    assert issubclass(GraphQuestionRewriteOutput, QuestionRewriteOutputBase)
    assert issubclass(GraphIntentRecognitionOutput, NaturalLanguageIntentOutputBase)

    rewrite = GraphQuestionRewriteOutput(rewritten_question="本月销售额")
    intent = GraphIntentRecognitionOutput(
        intent_type="metric_query",
        confidence=0.9,
        metric_mentions=["销售额"],
        dimension_slots=[
            {
                "name": "城市",
                "role": "group_by",
                "value": None,
                "value_status": "not_provided",
            }
        ],
    )

    assert rewrite.model_dump() == {
        "rewritten_question": "本月销售额",
        "need_user_input": False,
        "missing_slots": [],
        "image_profile_hint": None,
    }
    assert intent.model_dump()["dimension_slots"] == [
        {
            "name": "城市",
            "role": "group_by",
            "value": None,
            "value_status": "not_provided",
        }
    ]
    assert "value_confidence" not in intent.model_dump()["dimension_slots"][0]


def test_agent_rewrite_output_only_contains_original_and_rewrite_question():
    output = QuestionRewriteOutput(
        original_question="那上个月呢？",
        rewrite_question="查询上个月销售额",
        metric_phrases=["销售额"],
        dimension_phrases=[],
    )

    assert output.model_dump() == {
        "original_question": "那上个月呢？",
        "rewrite_question": "查询上个月销售额",
        "metric_phrases": ["销售额"],
        "dimension_phrases": [],
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        QuestionRewriteOutput.model_validate(
            {
                "original_question": "那上个月呢？",
                "rewrite_question": "查询上个月销售额",
                "message_type": "followup",
            }
        )


def test_dimension_recognition_requires_one_slot_for_each_unique_mention():
    output = DimensionRecognitionOutput.model_validate(
        {
            "dimension_mentions": ["城市", "城市"],
            "dimension_slots": [{"name": "城市", "role": "group_by"}],
        }
    )

    assert output.dimension_mentions == ["城市"]
    with pytest.raises(ValidationError, match="必须一一对应"):
        DimensionRecognitionOutput.model_validate(
            {
                "dimension_mentions": ["城市", "渠道"],
                "dimension_slots": [{"name": "城市", "role": "group_by"}],
            }
        )
