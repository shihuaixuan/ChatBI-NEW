import orjson
import pytest

from apps.chatbi.models import QuestionModelResponse
from apps.chatbi.services.understanding.understanding_service import (
    QuestionUnderstandingService,
)


def test_question_understanding_service_requires_explicit_model_boundary():
    with pytest.raises(
        ValueError,
        match="QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED",
    ):
        QuestionUnderstandingService()


class SequenceQuestionModel:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def invoke(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        payload = self._payloads.pop(0)
        return QuestionModelResponse(
            content=orjson.dumps(payload).decode(),
            usage_metadata={"total_tokens": 10},
        )


class FakeDimension:
    def __init__(self, name, alias, data_type, is_time=False):
        self.name = name
        self.alias = alias
        self.ext_info = {
            "dimension_type": "partition_time" if is_time else "primary_key",
            "dimension_data_type": data_type,
            "is_default_time": is_time,
        }
        self.type_params = {"timeGranularity": "day"} if is_time else {}


class FakeSchemaProvider:
    def build_dataset_schema(self, oid, dataset_id):
        assert (oid, dataset_id) == (1, 243)
        return type(
            "Schema",
            (),
            {
                "dimensions": [
                    FakeDimension("统计日期", [], "date", is_time=True),
                    FakeDimension("档口ID", ["店铺", "档口", "门店"], "bigint"),
                    FakeDimension("商品ID", [], "varchar"),
                ]
            },
        )()


def _rewrite_payload(question):
    return {
        "message_type": "new_question",
        "rewritten_question": question,
        "inherited_context": {},
        "need_user_input": False,
        "missing_slots": [],
        "confidence": 0.98,
    }


def _intent_payload(metric):
    return {
        "intent_type": "metric_query",
        "confidence": 0.95,
        "metric_mentions": [metric],
        "dimension_mentions": [],
        "dimension_slots": [],
        "time_mentions": [],
        "time_range": {"raw": "2026年6月30日", "value_status": "provided"},
        "filter_mentions": [],
        "required_slot_types": [],
        "query_shape": {},
        "ambiguous_slots": [],
        "conflict_slots": [],
    }


def test_agent_understanding_retries_invalid_filter_mentions_and_uses_schema_aliases():
    question = "2026年6月30日店铺100011的客户数是多少？"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload("客户数"),
            {
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "店铺100011",
                        "value_status": "provided",
                        "value_confidence": 0.95,
                    }
                ],
                "filter_mentions": ["店铺100011"],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            {
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    }
                ],
                # 兼容模型仍使用旧字段名；归一化后与槽位重复的条件会被去除。
                "filter_mentions": [{"name": "店铺", "value": "100011"}],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        schema_provider=FakeSchemaProvider(),
    ).understand(
        question=question,
        datasource_id=13,
        tenant_id=1,
        dataset_id=243,
    )

    slot = outcome.output.intent.dimension_slots[0]
    assert (slot.name, slot.value, slot.role) == ("档口ID", "100011", "filter")
    assert outcome.output.intent.filter_mentions == []
    assert outcome.output.intent.time_mentions == ["2026年6月30日"]
    assert outcome.output.intent.required_slot_types == [
        "metric",
        "filter",
        "time_dimension",
    ]
    assert outcome.output.validation.status == "valid"
    assert len(model.calls) == 4
    first_dimension_input = orjson.loads(model.calls[2][1])
    assert first_dimension_input["available_dimensions"][0]["name"] == "档口ID"
    assert "店铺" in first_dimension_input["available_dimensions"][0]["aliases"]
    retry_input = orjson.loads(model.calls[3][1])
    assert retry_input["repair_feedback"]["reason_code"] == (
        "DIMENSION_RECOGNITION_MODEL_OUTPUT_INVALID"
    )


def test_agent_understanding_normalizes_alphanumeric_id_value():
    question = "2026年6月30日档口100011商品ID为P1000101001的库存是多少？"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload("当前库存件数"),
            {
                "dimension_mentions": ["档口", "商品ID"],
                "dimension_slots": [
                    {
                        "name": "档口",
                        "role": "filter",
                        "value": "档口=100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                    {
                        "name": "商品ID",
                        "role": "filter",
                        "value": "商品ID为P1000101001",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                ],
                "residual_filter_mentions": [],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        schema_provider=FakeSchemaProvider(),
    ).understand(
        question=question,
        datasource_id=13,
        tenant_id=1,
        dataset_id=243,
    )

    assert [
        (slot.name, slot.value) for slot in outcome.output.intent.dimension_slots
    ] == [
        ("档口ID", "100011"),
        ("商品ID", "P1000101001"),
    ]


def test_agent_understanding_serializes_model_validator_error_for_repair():
    question = "2026年6月30日店铺100011的客户数是多少？"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload("客户数"),
            {
                "dimension_mentions": ["店铺"],
                "dimension_slots": [],
                "residual_filter_mentions": [],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            {
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    }
                ],
                "residual_filter_mentions": [],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        schema_provider=FakeSchemaProvider(),
    ).understand(
        question=question,
        datasource_id=13,
        tenant_id=1,
        dataset_id=243,
    )

    assert outcome.output.intent.dimension_slots[0].value == "100011"
    repair_feedback = orjson.loads(model.calls[3][1])["repair_feedback"]
    assert repair_feedback["validation_errors"] == [
        {
            "type": "value_error",
            "loc": [],
            "msg": "Value error, dimension_mentions 与 dimension_slots 必须一一对应",
        }
    ]
