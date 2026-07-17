from apps.workflow.capabilities.adapters.interaction import InteractionAdapter
from apps.workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.workflow.capabilities.real import RealChatBICapabilityGateway
from apps.semantic.schemas import DatasetSchema, SchemaElement


class FakeSemanticSchemaBuilder:
    def __init__(self, schema: DatasetSchema) -> None:
        self.schema = schema
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema:
        self.calls.append((oid, dataset_id))
        return self.schema


def _v1_request(variables: dict) -> dict:
    return {
        "request": {"question": "看一下情况", "tenant_id": 10, "user_id": 20, "dataset_id": 30},
        "conversation": {},
        "variables": variables,
        "inputs": {},
    }


def test_interaction_adapter_builds_rewrite_clarification_from_missing_slots():
    adapter = InteractionAdapter()

    result = adapter.ask_rewrite_clarification(
        _v1_request({"rewrite": {"missing_slots": ["metric", "time_range"]}})
    )

    assert result == {
        "prompt": "请补充要分析的指标和时间范围。",
        "options": [
            {"label": "访问人数", "value": {"metric": "访问人数"}},
            {"label": "销售额", "value": {"metric": "销售额"}},
            {"label": "订单数", "value": {"metric": "订单数"}},
            {"label": "今天", "value": {"time_range": "今天"}},
            {"label": "最近 7 天", "value": {"time_range": "最近 7 天"}},
            {"label": "本月", "value": {"time_range": "本月"}},
        ],
        "response_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "time_range": {"type": "string"},
                "skipped": {"type": "boolean"},
            },
            "x-card": {
                "card_type": "clarification",
                "clarification_type": "rewrite_slots",
                "input_type": "single_select_with_text",
                "question_key": "rewrite:metric,time_range",
            },
        },
        "allowed_update_paths": ["variables.rewrite_response"],
    }


def test_interaction_adapter_builds_cross_model_split_confirmation():
    result = InteractionAdapter().ask_cross_model_split(
        _v1_request(
            {
                "knowledge": {
                    "status": "cross_model",
                    "multi_query_plans": [
                        {"model_id": 10, "model_name": "档口订单", "metrics": ["总GMV"]},
                        {"model_id": 11, "model_name": "商品库存", "metrics": ["库存量"]},
                    ],
                }
            }
        )
    )

    assert result["prompt"] == "问题包含不同模型的指标，直接关联可能造成重复计算。是否拆分为独立查询？"
    assert result["options"] == [
        {"label": "拆分查询", "value": {"cross_model_action": "split"}},
        {"label": "取消查询", "value": {"cross_model_action": "cancel"}},
    ]
    assert result["allowed_update_paths"] == ["variables.cross_model_response"]


def test_interaction_adapter_prefers_knowledge_candidates_for_rewrite_options():
    adapter = InteractionAdapter()

    result = adapter.ask_rewrite_clarification(
        _v1_request(
            {
                "rewrite": {"missing_slots": ["metric", "dimension"]},
                "knowledge": {
                    "candidate_groups": {
                        "metrics": [
                            {"asset_id": 100, "display_name": "访问人数", "biz_name": "visit_uv"},
                            {"asset_id": 101, "display_name": "访问次数", "biz_name": "visit_pv"},
                        ],
                        "dimensions": [
                            {"asset_id": 200, "display_name": "统计日期", "biz_name": "stat_date"},
                        ],
                    }
                },
            }
        )
    )

    assert result["options"] == [
        {"label": "访问人数", "value": {"metric": "访问人数", "asset_id": 100}},
        {"label": "访问次数", "value": {"metric": "访问次数", "asset_id": 101}},
        {"label": "统计日期", "value": {"dimension": "统计日期", "asset_id": 200}},
    ]


def test_interaction_adapter_loads_schema_candidates_when_knowledge_is_absent():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=30,
            data_set_name="店铺明细数据集",
            id=30,
            name="店铺明细数据集",
            biz_name="stall_dataset",
            type="DATASET",
        ),
        metrics=[
            SchemaElement(
                data_set_id=30,
                data_set_name="店铺明细数据集",
                id=100,
                name="访问人数",
                biz_name="visit_uv",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=30,
                data_set_name="店铺明细数据集",
                id=200,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
            )
        ],
    )
    schema_builder = FakeSemanticSchemaBuilder(schema)
    adapter = InteractionAdapter(schema_builder=schema_builder)

    result = adapter.ask_rewrite_clarification(
        _v1_request({"rewrite": {"missing_slots": ["metric", "dimension"]}})
    )

    assert schema_builder.calls == [(10, 30)]
    assert result["options"] == [
        {"label": "访问人数", "value": {"metric": "访问人数", "asset_id": 100}},
        {"label": "统计日期", "value": {"dimension": "统计日期", "asset_id": 200}},
    ]


def test_interaction_adapter_builds_intent_clarification_options():
    adapter = InteractionAdapter()

    result = adapter.ask_intent_clarification(
        _v1_request(
            {
                "intent": {
                    "intent_type": "unknown",
                    "confidence": 0.4,
                    "ambiguous_slots": ["intent_type"],
                    "conflict_slots": [],
                }
            }
        )
    )

    assert result["prompt"] == "请确认你想进行哪类分析。"
    assert result["options"] == [
        {"label": "查指标数值", "value": {"intent": "metric_query"}},
        {"label": "看趋势", "value": {"intent": "trend_analysis"}},
        {"label": "看排名", "value": {"intent": "ranking_analysis"}},
        {"label": "看对比", "value": {"intent": "comparison_analysis"}},
        {"label": "看明细", "value": {"intent": "detail_query"}},
    ]
    assert result["allowed_update_paths"] == ["variables.intent_response"]
    assert result["response_schema"]["x-card"] == {
        "card_type": "clarification",
        "clarification_type": "intent",
        "input_type": "single_select_with_text",
        "question_key": "intent:intent_type",
    }


def test_interaction_adapter_builds_dimension_slot_clarification_card():
    adapter = InteractionAdapter()

    result = adapter.ask_slot_clarification(
        _v1_request(
            {
                "intent": {
                    "intent_type": "metric_query",
                    "confidence": 0.95,
                    "ambiguous_slots": [],
                    "validation": {
                        "clarification_required": True,
                        "slot_issues": [
                            {
                                "slot_type": "dimension_value",
                                "dimension": "店铺",
                                "role": "ambiguous",
                                "value_status": "not_provided",
                                "reason": "用户提到了店铺维度，但没有提供具体值或分组方式",
                            }
                        ],
                    },
                    "dimension_mentions": ["店铺"],
                    "dimension_slots": [
                        {
                            "name": "店铺",
                            "role": "ambiguous",
                            "value": None,
                            "value_status": "not_provided",
                        }
                    ],
                    "conflict_slots": [],
                }
            }
        )
    )

    assert result["prompt"] == "请确认“店铺”这个维度的使用方式。"
    assert result["options"] == [
        {"label": "按店铺分组查看", "value": {"dimension": "店铺", "dimension_usage": "group_by"}},
        {
            "label": "筛选某个具体店铺",
            "value": {
                "dimension": "店铺",
                "dimension_usage": "filter_value_required",
                "dimension_value_fields": ["店铺"],
            },
        },
        {"label": "不使用店铺维度", "value": {"dimension": "店铺", "dimension_usage": "ignore"}},
    ]
    assert result["allowed_update_paths"] == ["variables.slot_response"]
    assert result["response_schema"]["properties"] == {
        "dimension": {"type": "string"},
        "dimension_usage": {"type": "string"},
        "dimension_values": {"type": "object", "additionalProperties": {"type": "string"}},
        "skipped": {"type": "boolean"},
    }
    assert result["response_schema"]["x-card"] == {
        "card_type": "clarification",
        "clarification_type": "dimension_usage",
        "input_type": "dimension_value_form",
        "question_key": "dimension_usage:店铺",
        "dimension_value_fields": ["店铺"],
    }


def test_interaction_adapter_builds_subject_domain_slot_clarification_card():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=30,
            data_set_name="经营分析",
            id=30,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        subject_domains=[
            {"domain_id": 1, "name": "店铺", "biz_name": "shop", "description": "店铺和档口主题", "model_ids": [10]},
            {"domain_id": 2, "name": "商品", "biz_name": "product", "description": "商品经营主题", "model_ids": [11]},
        ],
    )
    schema_builder = FakeSemanticSchemaBuilder(schema)
    adapter = InteractionAdapter(schema_builder=schema_builder)

    result = adapter.ask_slot_clarification(
        _v1_request(
            {
                "intent": {
                    "intent_type": "metric_query",
                    "confidence": 0.95,
                    "ambiguous_slots": ["subject_domain"],
                    "validation": {
                        "clarification_required": True,
                        "slot_issues": [
                            {
                                "slot_type": "subject_domain",
                                "reason": "店铺和商品主题都可能匹配",
                                "candidate_domain_ids": [1, 2],
                            }
                        ],
                    },
                    "subject_domain": {
                        "status": "ambiguous",
                        "candidate_domain_ids": [1, 2],
                        "reason": "店铺和商品主题都可能匹配",
                    },
                    "conflict_slots": [],
                }
            }
        )
    )

    assert schema_builder.calls == [(10, 30)]
    assert result["prompt"] == "请确认这个问题属于哪个主题域。"
    assert result["options"] == [
        {"label": "店铺", "value": {"subject_domain": "店铺", "domain_id": 1}},
        {"label": "商品", "value": {"subject_domain": "商品", "domain_id": 2}},
    ]
    assert result["allowed_update_paths"] == ["variables.slot_response"]
    assert result["response_schema"]["properties"] == {
        "subject_domain": {"type": "string"},
        "domain_id": {"type": "integer"},
        "skipped": {"type": "boolean"},
    }
    assert result["response_schema"]["x-card"] == {
        "card_type": "clarification",
        "clarification_type": "subject_domain",
        "input_type": "single_select_with_text",
        "question_key": "subject_domain:1,2",
    }


def test_interaction_adapter_builds_metric_selection_options_from_ambiguity():
    adapter = InteractionAdapter()

    result = adapter.ask_metric_selection(
        _v1_request(
            {
                "knowledge": {
                    "ambiguities": [
                        {
                            "type": "metric",
                            "candidates": [
                                {"asset_id": 100, "display_name": "访问人数", "biz_name": "visit_uv"},
                                {"asset_id": 101, "display_name": "访问次数", "biz_name": "visit_pv"},
                            ],
                        }
                    ]
                }
            }
        )
    )

    assert result["prompt"] == "请选择要分析的指标。"
    assert result["options"] == [
        {"label": "访问人数", "value": 100},
        {"label": "访问次数", "value": 101},
    ]
    assert result["allowed_update_paths"] == ["variables.metric_selection"]


def test_interaction_adapter_falls_back_to_metric_candidate_group_for_metric_selection():
    adapter = InteractionAdapter()

    result = adapter.ask_metric_selection(
        _v1_request(
            {
                "knowledge": {
                    "ambiguities": [{"type": "metric", "candidates": []}],
                    "candidate_groups": {
                        "metrics": [
                            {"asset_id": 100, "display_name": "访问人数", "biz_name": "visit_uv"},
                            {"asset_id": 101, "display_name": "访问次数", "biz_name": "visit_pv"},
                        ]
                    },
                }
            }
        )
    )

    assert result["options"] == [
        {"label": "访问人数", "value": 100},
        {"label": "访问次数", "value": 101},
    ]


def test_real_gateway_routes_interaction_capabilities_to_real_adapter():
    gateway = RealChatBICapabilityGateway(fallback_gateway=PlaceholderChatBICapabilityGateway())

    result = gateway.invoke(
        "interaction.ask_intent_clarification",
        _v1_request({"intent": {"confidence": 0.3, "ambiguous_slots": ["intent_type"], "conflict_slots": []}}),
        "run:intent",
    )

    assert result["prompt"] == "请确认你想进行哪类分析。"
