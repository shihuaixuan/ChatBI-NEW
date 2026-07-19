import pytest
from pydantic import ValidationError

from apps.chatbi.models.dto.question_understanding import (
    DimensionRecognitionOutput,
    IntentRecognitionOutput,
    NaturalLanguageIntentOutputBase,
    QuestionRewriteOutputBase,
)
from apps.workflow.schemas.v1 import (
    IntentRecognitionOutput as GraphIntentRecognitionOutput,
)
from apps.workflow.schemas.v1 import (
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
        }
    )

    assert intent.dimension_slots[0].name == "城市"
    assert intent.time_range.raw == "本月"
    with pytest.raises(ValidationError):
        IntentRecognitionOutput.model_validate(
            {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "dimension_slots": [{"name": "城市", "role": "unsupported"}],
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
