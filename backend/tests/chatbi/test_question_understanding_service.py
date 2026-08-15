import orjson
import pytest

from apps.chatbi.errors import QuestionUnderstandingError
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


def test_understanding_preserves_repair_model_call_diagnostics():
    model = InvalidThenFailQuestionModel()

    with pytest.raises(QuestionUnderstandingError) as error_info:
        QuestionUnderstandingService(model).understand(
            question="本月销售额", datasource_id=5
        )

    assert "REWRITE_MODEL_OUTPUT_INVALID" in str(error_info.value)
    assert error_info.value.details["stage"] == "QUESTION_REWRITE"
    assert error_info.value.details["exception_type"] == "TimeoutError"
    assert model.calls == 2


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


class InvalidThenFailQuestionModel:
    """模拟首次结构错误、修复调用超时的模型客户端。"""

    def __init__(self):
        self.calls = 0

    def invoke(self, system_prompt, user_prompt):
        self.calls += 1
        if self.calls == 1:
            return QuestionModelResponse(
                content=orjson.dumps({"invalid": True}).decode(),
                usage_metadata={"total_tokens": 10},
            )
        raise TimeoutError("provider timeout")


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
    def __init__(self, *, include_overtime=False):
        self._include_overtime = include_overtime

    def build_dataset_schema(self, oid, dataset_id):
        assert (oid, dataset_id) == (1, 243)
        dimensions = [
            FakeDimension("统计日期", [], "date", is_time=True),
            FakeDimension("档口ID", ["店铺", "档口", "门店"], "bigint"),
            FakeDimension("商品ID", [], "varchar"),
            FakeDimension("客户ID", ["客户"], "varchar"),
            FakeDimension("客户名称", ["客户"], "varchar"),
            FakeDimension(
                "交易渠道，如线上或线下",
                ["渠道", "线上", "线下"],
                "varchar",
            ),
        ]
        if self._include_overtime:
            dimensions.append(FakeDimension("是否超时", ["超时"], "boolean"))
        return type(
            "Schema",
            (),
            {
                "dimensions": dimensions
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
        "query_shape": {"select_mode": "aggregate"},
        "ambiguous_slots": [],
        "conflict_slots": [],
    }


def test_detail_query_reclassifies_dimension_mentioned_as_metric():
    question = (
        "2026年6月30日店铺100011的未发订单号USO202606300001的"
        "订单金额、未发件数和是否超时是多少？"
    )
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                "intent_type": "detail_query",
                "confidence": 0.95,
                "metric_mentions": ["订单金额", "未发件数", "是否超时"],
                "dimension_mentions": [],
                "dimension_slots": [],
                "time_mentions": ["2026年6月30日"],
                "time_range": {
                    "raw": "2026年6月30日",
                    "value_status": "provided",
                },
                "filter_mentions": [],
                "required_slot_types": [],
                "query_shape": {
                    "select_mode": "detail",
                    "needs_group_by": False,
                    "needs_order_by": False,
                    "order_direction": None,
                    "limit": None,
                    "time_grain": None,
                },
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            {
                "dimension_mentions": ["店铺", "未发订单号"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                    {
                        "name": "未发订单号",
                        "role": "filter",
                        "value": "USO202606300001",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                ],
                "filter_mentions": [],
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        schema_provider=FakeSchemaProvider(include_overtime=True),
    ).understand(
        question=question,
        datasource_id=13,
        tenant_id=1,
        dataset_id=243,
    )

    intent = outcome.output.intent
    assert intent.metric_mentions == ["订单金额", "未发件数"]
    assert ("是否超时", "display") in [
        (slot.name, slot.role) for slot in intent.dimension_slots
    ]
    assert "是否超时" in intent.dimension_mentions
    assert outcome.output.validation.status == "valid"


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
    dimension_calls = [
        call for call in model.calls if "维度槽位识别器" in call[0]
    ]
    first_dimension_input = orjson.loads(dimension_calls[0][1])
    assert first_dimension_input["available_dimensions"][0]["name"] == "档口ID"
    assert "店铺" in first_dimension_input["available_dimensions"][0]["aliases"]
    assert "metric_mentions" not in first_dimension_input
    assert "time_mentions" not in first_dimension_input
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


def test_agent_understanding_preserves_model_ranking_semantics():
    question = "2026年6月30日当前库存件数最高的5个商品ID"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                "intent_type": "ranking_analysis",
                "confidence": 0.95,
                "metric_mentions": ["当前库存件数"],
                "dimension_mentions": [],
                "dimension_slots": [],
                "time_mentions": ["2026年6月30日", "当前"],
                "time_range": {
                    "raw": "2026年6月30日",
                    "value_status": "provided",
                },
                "filter_mentions": [],
                "required_slot_types": [],
                "query_shape": {
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "needs_order_by": True,
                    "order_direction": "desc",
                    "limit": 5,
                },
                "ambiguous_slots": [],
                "conflict_slots": ["time_range"],
            },
            {
                "dimension_mentions": ["商品ID"],
                "dimension_slots": [
                    {
                        "name": "商品ID",
                        "role": "group_by",
                        "value": None,
                        "value_status": "not_provided",
                        "value_confidence": 0.95,
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

    intent = outcome.output.intent
    assert intent.dimension_slots[0].role == "group_by"
    assert intent.ambiguous_slots == []
    assert intent.time_mentions == ["2026年6月30日"]
    assert intent.conflict_slots == []
    assert intent.required_slot_types == [
        "metric",
        "dimension",
        "time_dimension",
        "order",
        "limit",
    ]
    assert intent.query_shape.model_dump(exclude_none=True) == {
        "select_mode": "aggregate",
        "needs_group_by": True,
        "needs_order_by": True,
        "order_direction": "desc",
        "limit": 5,
    }
    assert outcome.output.validation.status == "valid"


def test_agent_understanding_does_not_infer_ranking_semantics_from_question_text():
    question = "当前库存件数最高的5个商品ID"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                "intent_type": "ranking_analysis",
                "confidence": 0.95,
                "metric_mentions": ["当前库存件数"],
                "dimension_mentions": [],
                "dimension_slots": [],
                "time_mentions": [],
                "time_range": {"raw": None, "value_status": "not_provided"},
                "filter_mentions": [],
                "required_slot_types": [],
                "query_shape": {"select_mode": "aggregate"},
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            {
                "dimension_mentions": ["商品ID"],
                "dimension_slots": [
                    {
                        "name": "商品ID",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "not_provided",
                        "value_confidence": 0.8,
                    }
                ],
                "residual_filter_mentions": [],
                "ambiguous_slots": ["商品ID"],
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

    intent = outcome.output.intent
    assert intent.dimension_slots[0].role == "ambiguous"
    assert intent.query_shape.model_dump(exclude_none=True) == {
        "select_mode": "aggregate",
        "needs_group_by": False,
        "needs_order_by": False,
    }
    assert {
        "dimension_role_ambiguous",
        "ranking_dimension_missing",
        "ranking_order_missing",
        "ranking_limit_missing",
    }.issubset(outcome.output.validation.reason_codes)


def test_agent_understanding_marks_multi_value_comparison_for_grouping():
    question = "比较店铺100011和100012的客户数"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                "intent_type": "comparison_analysis",
                "confidence": 0.95,
                "metric_mentions": ["客户数"],
                "dimension_mentions": [],
                "dimension_slots": [],
                "time_mentions": [],
                "time_range": {"raw": None, "value_status": "not_provided"},
                "filter_mentions": [],
                "required_slot_types": [],
                "query_shape": {
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                },
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            {
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": ["100011", "100012"],
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

    intent = outcome.output.intent
    assert intent.dimension_slots[0].value == ["100011", "100012"]
    assert intent.required_slot_types == [
        "metric",
        "filter",
        "comparison_target",
        "dimension",
    ]
    assert intent.query_shape.model_dump(exclude_none=True) == {
        "select_mode": "aggregate",
        "needs_group_by": True,
        "needs_order_by": False,
    }
    assert outcome.output.validation.status == "valid"


def test_agent_understanding_normalizes_object_ambiguity_without_losing_id_filters():
    question = "2026年6月30日店铺100011客户C1000101001的客户当日GMV是多少？"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload("客户当日GMV"),
            {
                "dimension_mentions": ["店铺", "客户ID"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                    {
                        "name": "客户ID",
                        "role": "filter",
                        "value": "C1000101001",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                ],
                "residual_filter_mentions": [],
                "ambiguous_slots": [
                    {
                        "name": "客户ID",
                        "role": "filter",
                        "value_status": "provided",
                    }
                ],
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
        (slot.name, slot.value)
        for slot in outcome.output.intent.dimension_slots
    ] == [
        ("档口ID", "100011"),
        ("客户ID", "C1000101001"),
    ]
    assert outcome.output.intent.ambiguous_slots == []
    assert outcome.output.validation.status == "valid"
    assert len(model.calls) == 3


def test_agent_understanding_repairs_repeated_dimension_coverage_mismatch():
    question = "2026年6月30日店铺100011商品P1000101001的当前库存件数是多少？"
    invalid_dimensions = {
        "dimension_mentions": ["店铺", "商品ID"],
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
    }
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload("当前库存件数"),
            invalid_dimensions,
            invalid_dimensions,
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

    assert outcome.output.intent.dimension_mentions == ["档口ID", "商品ID"]
    assert outcome.output.intent.dimension_slots[1].model_dump(mode="json") == {
        "name": "商品ID",
        "role": "ambiguous",
        "value": None,
        "value_status": "not_provided",
        "value_confidence": 0.0,
    }
    assert outcome.output.validation.status == "clarification_required"
    assert "dimension_role_ambiguous" in outcome.output.validation.reason_codes


def test_agent_understanding_does_not_silently_drop_independent_dimension_result():
    question = "2026年6月店铺100011线上和线下客户当日GMV占比分别是多少？"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                **_intent_payload("客户当日GMV"),
                "intent_type": "share_analysis",
                "time_range": {"raw": "2026年6月", "value_status": "provided"},
                "query_shape": {
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                },
            },
            {
                "dimension_mentions": [
                    "店铺",
                    "客户名称",
                    "交易渠道",
                ],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "filter",
                        "value": "100011",
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    },
                    {
                        "name": "客户名称",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "not_provided",
                        "value_confidence": 0.0,
                    },
                    {
                        "name": "交易渠道",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "not_provided",
                        "value_confidence": 0.0,
                    },
                ],
                "residual_filter_mentions": [],
                "ambiguous_slots": [{"name": "客户名称"}],
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

    intent = outcome.output.intent
    assert [slot.name for slot in intent.dimension_slots] == [
        "档口ID",
        "客户名称",
        "交易渠道，如线上或线下",
    ]
    assert intent.query_shape.needs_group_by is True
    assert "dimension" in intent.required_slot_types
    assert intent.ambiguous_slots == []
    assert outcome.output.validation.status == "clarification_required"
    assert "dimension_role_ambiguous" in outcome.output.validation.reason_codes
