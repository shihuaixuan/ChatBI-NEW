from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    CandidateGate,
    HeadlessKnowledgeAdapter,
)
from apps.headless.schemas import (
    DataSetSchema,
    SchemaElement,
    SchemaElementMatch,
    SchemaMapInfo,
)


class FakeHeadlessSchemaBuilder:
    def __init__(self, schema: DataSetSchema) -> None:
        self.schema = schema
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        self.calls.append((oid, dataset_id))
        return self.schema


class FakeSchemaMapper:
    def __init__(self, matches: list[SchemaElementMatch]) -> None:
        self.matches = matches

    def map_schema(self, query_text: str, schema: DataSetSchema) -> SchemaMapInfo:
        return SchemaMapInfo(data_set_element_matches={schema.data_set.id: self.matches})


class TextAwareSchemaMapper:
    def __init__(self, matches_by_text: dict[str, list[SchemaElementMatch]]) -> None:
        self.matches_by_text = matches_by_text
        self.queries: list[str] = []

    def map_schema(self, query_text: str, schema: DataSetSchema) -> SchemaMapInfo:
        self.queries.append(query_text)
        matches = self.matches_by_text.get(query_text, [])
        return SchemaMapInfo(data_set_element_matches={schema.data_set.id: matches})


class EmptyDocumentRetriever:
    def retrieve(self, query_text: str, schema: DataSetSchema, oid: int) -> dict[str, list[dict]]:
        return {"metrics": [], "dimensions": [], "values": [], "terms": []}


def test_candidate_gate_keeps_one_metric_for_each_explicit_mention():
    gate = CandidateGate()

    result = gate.decide(
        {
            "metrics": [
                {
                    "asset_id": 100,
                    "name": "总订单数",
                    "biz_name": "order_cnt_total",
                    "matched_text": "总订单数",
                    "score": 1.2,
                    "model_id": 10,
                },
                {
                    "asset_id": 101,
                    "name": "总GMV",
                    "biz_name": "gmv_total",
                    "matched_text": "总GMV",
                    "score": 1.15,
                    "model_id": 10,
                },
            ],
            "dimensions": [],
            "values": [],
            "terms": [],
        },
        expected_metric_mentions=["总订单数", "总GMV"],
    )

    assert result["status"] == "hit"
    assert [item["biz_name"] for item in result["selected_assets"]["metrics"]] == [
        "order_cnt_total",
        "gmv_total",
    ]


def test_selected_dimensions_are_pruned_to_explicit_query_roles():
    dimensions = [
        {
            "asset_id": 200,
            "name": "档口ID",
            "biz_name": "stall_id",
            "model_id": 10,
            "score": 1.1,
            "payload": {"alias": ["档口"], "ext_info": {}},
        },
        {
            "asset_id": 201,
            "name": "商家ID",
            "biz_name": "seller_id",
            "model_id": 10,
            "score": 0.9,
            "payload": {"ext_info": {}},
        },
        {
            "asset_id": 202,
            "name": "记录创建时间",
            "biz_name": "created_at",
            "model_id": 10,
            "score": 0.8,
            "payload": {"ext_info": {"dimension_type": "partition_time", "is_default_time": False}},
        },
    ]

    result = HeadlessKnowledgeAdapter._constrain_selected_dimensions_by_intent(
        {"metrics": [], "dimensions": dimensions, "values": [], "terms": []},
        {
            "dimension_slots": [
                {"name": "档口", "role": "group_by", "value_status": "not_provided"},
            ],
            "time_range": {"raw": None, "value_status": "not_provided"},
            "query_shape": {"needs_group_by": True},
        },
    )

    assert [item["biz_name"] for item in result["dimensions"]] == ["stall_id"]


def test_group_by_dimension_slot_triggers_dimension_fallback_without_required_slot_type():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=100,
        name="总GMV",
        biz_name="gmv_total",
        type="METRIC",
        fields=["gmv_total"],
    )
    dimension = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=200,
        name="店铺ID",
        biz_name="stall_id",
        type="DIMENSION",
        alias=["店铺"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "店铺订单模型", "biz_name": "stall_order", "tableQuery": "stall_order_daily"}],
        metrics=[metric],
        dimensions=[dimension],
    )
    mapper = TextAwareSchemaMapper(
        {
            "总GMV": [SchemaElementMatch(element=metric, similarity=0.96, detect_word="总GMV", word="总GMV")],
            "最近 30 天商城店铺的总 GMV 是多少？": [
                SchemaElementMatch(element=dimension, similarity=0.9, detect_word="店铺", word="商城店铺")
            ],
        }
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=mapper,
        document_retriever=EmptyDocumentRetriever(),
    )

    result = adapter.retrieve(
        {
            "request": {
                "question": "最近 30 天商城店铺的总 GMV 是多少？",
                "dataset_id": 20,
                "tenant_id": 10,
                "user_id": 20,
            },
            "variables": {
                "rewrite": {"rewritten_question": "最近 30 天商城店铺的总 GMV 是多少？"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["总GMV"],
                    "dimension_mentions": ["商城店铺"],
                    "dimension_slots": [
                        {"name": "商城店铺", "role": "group_by", "value": None, "value_status": "not_provided"}
                    ],
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate", "needs_group_by": False},
                },
            },
        }
    )

    assert result["hit"] is True
    assert [item["biz_name"] for item in result["selected_assets"]["dimensions"]] == ["stall_id"]
    assert result["slot_bindings"]["dimensions"][0]["biz_name"] == "stall_id"


def test_group_by_dimension_fallback_is_not_satisfied_by_time_dimensions():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=100,
        name="总GMV",
        biz_name="gmv_total",
        type="METRIC",
        fields=["gmv_total"],
    )
    time_dimension = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=201,
        name="统计日期",
        biz_name="stat_date",
        type="DIMENSION",
        ext_info={"dimension_type": "partition_time", "is_default_time": True},
    )
    store_dimension = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=200,
        name="店铺ID",
        biz_name="stall_id",
        type="DIMENSION",
        alias=["店铺"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "店铺订单模型", "biz_name": "stall_order", "tableQuery": "stall_order_daily"}],
        metrics=[metric],
        dimensions=[time_dimension, store_dimension],
    )
    mapper = TextAwareSchemaMapper(
        {
            "总GMV": [SchemaElementMatch(element=metric, similarity=0.96, detect_word="总GMV", word="总GMV")],
            "最近 30 天商城店铺的总 GMV 是多少？": [
                SchemaElementMatch(element=store_dimension, similarity=0.9, detect_word="店铺", word="商城店铺")
            ],
        }
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=mapper,
        document_retriever=EmptyDocumentRetriever(),
    )

    result = adapter.retrieve(
        {
            "request": {
                "question": "最近 30 天商城店铺的总 GMV 是多少？",
                "dataset_id": 20,
                "tenant_id": 10,
                "user_id": 20,
            },
            "variables": {
                "rewrite": {"rewritten_question": "最近 30 天商城店铺的总 GMV 是多少？"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["总GMV"],
                    "dimension_mentions": ["店铺"],
                    "dimension_slots": [
                        {"name": "店铺", "role": "group_by", "value": None, "value_status": "not_provided"}
                    ],
                    "time_mentions": ["最近 30 天"],
                    "time_range": {"raw": "最近 30 天", "value_status": "provided"},
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate", "needs_group_by": False},
                },
            },
        }
    )

    selected_dimension_biz_names = [item["biz_name"] for item in result["selected_assets"]["dimensions"]]
    assert "stall_id" in selected_dimension_biz_names
    assert "stat_date" in selected_dimension_biz_names
    assert [item["biz_name"] for item in result["selected_assets"]["business_dimensions"]] == ["stall_id"]
    assert [item["biz_name"] for item in result["selected_assets"]["time_dimensions"]] == ["stat_date"]
    assert [item["biz_name"] for item in result["slot_bindings"]["group_dimensions"]] == ["stall_id"]
    assert [item["biz_name"] for item in result["slot_bindings"]["time_dimensions"]] == ["stat_date"]
    assert [item["biz_name"] for item in result["slot_bindings"]["time_filters"]] == ["stat_date"]


def test_current_snapshot_time_does_not_become_group_dimension():
    dimensions = [
        {
            "asset_id": 200,
            "name": "档口ID",
            "biz_name": "stall_id",
            "model_id": 10,
            "score": 1.1,
            "payload": {"alias": ["档口"], "ext_info": {}},
        },
        {
            "asset_id": 201,
            "name": "快照日期",
            "biz_name": "snapshot_date",
            "model_id": 10,
            "score": 0.88,
            "payload": {"ext_info": {"dimension_type": "partition_time", "is_default_time": True}},
        },
    ]

    result = HeadlessKnowledgeAdapter._constrain_selected_dimensions_by_intent(
        {"metrics": [], "dimensions": dimensions, "values": [], "terms": []},
        {
            "dimension_slots": [
                {"name": "档口", "role": "group_by", "value_status": "not_provided"},
            ],
            "time_range": {
                "raw": None,
                "value_status": "not_provided",
            },
            "time_mentions": ["当前"],
            "required_slot_types": ["metric", "dimension", "time_dimension"],
            "query_shape": {"needs_group_by": True},
        },
    )

    assert [item["biz_name"] for item in result["dimensions"]] == ["stall_id"]


def test_cross_model_metrics_are_grouped_into_independent_query_plans():
    selected_assets = {
        "metrics": [
            {"asset_id": 100, "model_id": 10, "biz_name": "gmv_total", "name": "总GMV"},
            {"asset_id": 101, "model_id": 11, "biz_name": "stock_qty", "name": "库存量"},
        ],
        "dimensions": [
            {"asset_id": 200, "model_id": 10, "biz_name": "stall_id", "name": "档口ID"},
            {"asset_id": 201, "model_id": 11, "biz_name": "stall_id", "name": "档口ID"},
        ],
        "values": [],
        "terms": [],
    }

    plans = HeadlessKnowledgeAdapter._cross_model_query_plans(selected_assets)

    assert [plan["model_id"] for plan in plans] == [10, 11]
    assert plans[0]["metric_ids"] == [100]
    assert plans[1]["metric_ids"] == [101]
    assert plans[0]["dimension_ids"] == [200]
    assert plans[1]["dimension_ids"] == [201]


def test_headless_knowledge_adapter_retrieves_metric_candidates_from_dataset_schema():
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "流量模型",
                "biz_name": "traffic_model",
                "datasource_id": 7001,
                "tableQuery": "traffic_daily",
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="访问量",
                biz_name="visit_uv",
                type="METRIC",
                alias=["访问人数"],
                default_agg="SUM",
                fields=["visit_uv"],
            )
        ],
        dimensions=[],
    )
    schema_builder = FakeHeadlessSchemaBuilder(schema)
    adapter = HeadlessKnowledgeAdapter(schema_builder=schema_builder)

    result = adapter.retrieve(
        {
            "request": {"question": "今日访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今日访问人数"},
                "intent": {"intent_type": "metric_query", "confidence": 0.95},
            },
        }
    )

    assert schema_builder.calls == [(10, 20)]
    assert result["hit"] is True
    assert result["status"] == "hit"
    assert result["dataset_id"] == 20
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert result["selected_assets"]["metrics"][0]["biz_name"] == "visit_uv"
    assert result["candidate_groups"]["metrics"][0]["matched_text"] == "访问人数"


def test_headless_knowledge_adapter_uses_intent_metric_mentions_before_rewritten_question():
    visit_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="访问人数",
        biz_name="visit_uv",
        type="METRIC",
        alias=["访问量"],
        default_agg="SUM",
        fields=["visit_uv"],
    )
    order_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=101,
        name="订单数",
        biz_name="order_cnt",
        type="METRIC",
        default_agg="SUM",
        fields=["order_cnt"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "经营模型", "biz_name": "business_model", "tableQuery": "business_daily"}],
        metrics=[visit_metric, order_metric],
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(schema_builder=FakeHeadlessSchemaBuilder(schema))

    result = adapter.retrieve(
        {
            "request": {"question": "访问人数和订单数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "访问人数和订单数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["订单数"],
                    "required_slot_types": ["metric"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 101
    assert [item["asset_id"] for item in result["candidate_groups"]["metrics"]] == [101]


def test_headless_knowledge_adapter_adds_time_dimension_from_time_mentions():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="访问人数",
        biz_name="visit_uv",
        type="METRIC",
        alias=["访问量"],
        default_agg="SUM",
        fields=["visit_uv"],
    )
    stat_date = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=200,
        name="统计日期",
        biz_name="stat_date",
        type="DIMENSION",
        ext_info={"dimension_type": "partition_time", "is_default_time": True},
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "流量模型",
                "biz_name": "traffic_model",
                "tableQuery": "traffic_daily",
            }
        ],
        metrics=[metric],
        dimensions=[stat_date],
    )
    adapter = HeadlessKnowledgeAdapter(schema_builder=FakeHeadlessSchemaBuilder(schema))

    result = adapter.retrieve(
        {
            "request": {"question": "最近 7 天访问人数趋势", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "最近 7 天访问人数趋势"},
                "intent": {
                    "intent_type": "trend_analysis",
                    "metric_mentions": ["访问人数"],
                    "time_mentions": ["最近 7 天"],
                    "required_slot_types": ["metric", "time_dimension"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert result["selected_assets"]["dimensions"][0]["asset_id"] == 200
    assert result["slot_bindings"]["dimensions"][0]["source"] == "intent_time_dimension"


def test_headless_knowledge_adapter_binds_dimension_filter_from_intent_slot():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="档口经营分析",
        model=10,
        id=100,
        name="访问人数",
        biz_name="visit_uv",
        type="METRIC",
        fields=["visit_uv"],
    )
    stall = SchemaElement(
        data_set_id=20,
        data_set_name="档口经营分析",
        model=10,
        id=200,
        name="档口",
        biz_name="stall_id",
        type="DIMENSION",
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "档口流量模型", "biz_name": "stall_traffic", "tableQuery": "stall_traffic_1d"}],
        metrics=[metric],
        dimensions=[stall],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [
                SchemaElementMatch(element=metric, similarity=0.96, detect_word="访问人数", word="访问人数"),
                SchemaElementMatch(element=stall, similarity=0.96, detect_word="档口", word="档口"),
            ]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今天档口1的访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今天档口1的访问人数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["访问人数"],
                    "dimension_mentions": ["档口"],
                    "dimension_slots": [{"name": "档口", "role": "filter", "value": "1", "value_status": "provided"}],
                    "required_slot_types": ["metric"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["slot_bindings"]["filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "display_name": "档口",
            "biz_name": "stall_id",
            "operator": "=",
            "value": "1",
            "confidence": 1.3,
            "source": "intent_dimension_slot",
        }
    ]


def test_headless_knowledge_adapter_binds_dimension_filter_from_filter_mentions():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=100,
        name="客户数",
        biz_name="customer_cnt",
        type="METRIC",
        fields=["customer_cnt"],
    )
    stall = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=200,
        name="店铺ID",
        biz_name="stall_id",
        type="DIMENSION",
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "店铺客户模型", "biz_name": "stall_customer", "tableQuery": "stall_customer_1d"}],
        metrics=[metric],
        dimensions=[stall],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [
                SchemaElementMatch(element=metric, similarity=0.96, detect_word="客户数", word="客户数"),
                SchemaElementMatch(element=stall, similarity=0.96, detect_word="店铺", word="店铺"),
            ]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今天店铺1的客户数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今天店铺1的客户数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["客户数"],
                    "dimension_mentions": [],
                    "dimension_slots": [],
                    "filter_mentions": [{"name": "店铺", "value": "1"}],
                    "required_slot_types": ["metric"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["slot_bindings"]["filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "display_name": "店铺ID",
            "biz_name": "stall_id",
            "operator": "=",
            "value": "1",
            "confidence": 1.0,
            "source": "intent_filter_mention",
        }
    ]


def test_headless_knowledge_adapter_strips_dimension_name_from_filter_value():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=100,
        name="累积线上总客户数",
        biz_name="total_customer_cnt_online",
        type="METRIC",
        fields=["total_customer_cnt_online"],
    )
    stall = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=200,
        name="店铺ID",
        biz_name="stall_id",
        type="DIMENSION",
        alias=["店铺"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "店铺客户模型", "biz_name": "stall_customer", "tableQuery": "stall_customer_1d"}],
        metrics=[metric],
        dimensions=[stall],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [
                SchemaElementMatch(element=metric, similarity=0.96, detect_word="客户数", word="客户数"),
                SchemaElementMatch(element=stall, similarity=0.96, detect_word="店铺", word="店铺"),
            ]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "店铺1的累积线上总客户数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "店铺1的累积线上总客户数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["累积线上总客户数"],
                    "dimension_mentions": ["店铺ID"],
                    "dimension_slots": [{"name": "店铺ID", "role": "filter", "value": "店铺1", "value_status": "provided"}],
                    "required_slot_types": ["metric"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["slot_bindings"]["filters"][0]["value"] == "1"


def test_headless_knowledge_adapter_binds_today_time_range_to_default_time_dimension_filter():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="档口经营分析",
        model=10,
        id=100,
        name="访问人数",
        biz_name="visit_uv",
        type="METRIC",
        fields=["visit_uv"],
    )
    stat_date = SchemaElement(
        data_set_id=20,
        data_set_name="档口经营分析",
        model=10,
        id=201,
        name="统计日期",
        biz_name="stat_date",
        type="DIMENSION",
        ext_info={"dimension_type": "partition_time", "is_default_time": True},
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="档口经营分析",
            id=20,
            name="档口经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "档口流量模型", "biz_name": "stall_traffic", "tableQuery": "stall_traffic_1d"}],
        metrics=[metric],
        dimensions=[stat_date],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [SchemaElementMatch(element=metric, similarity=0.96, detect_word="访问人数", word="访问人数")]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今天访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今天访问人数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["访问人数"],
                    "time_mentions": ["今天"],
                    "time_range": {"raw": "今天", "value_status": "provided"},
                    "required_slot_types": ["metric", "time_range"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["slot_bindings"]["filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 201,
            "display_name": "统计日期",
            "biz_name": "stat_date",
            "operator": "=",
            "value": {
                "kind": "single_date",
                "anchor": "today",
                "offset_days": 0,
                "timezone": "Asia/Shanghai",
            },
            "confidence": 0.88,
            "source": "intent_time_range",
        }
    ]


def test_headless_knowledge_adapter_misses_when_only_time_dimension_is_from_unrelated_model():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=10,
        id=100,
        name="访问人数",
        biz_name="visit_uv",
        type="METRIC",
        fields=["visit_uv"],
    )
    other_model_time = SchemaElement(
        data_set_id=20,
        data_set_name="店铺经营分析",
        model=11,
        id=201,
        name="时间",
        biz_name="stat_date",
        type="DIMENSION",
        ext_info={"dimension_type": "partition_time", "is_default_time": True},
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="店铺经营分析",
            id=20,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        models=[
            {"id": 10, "name": "旧流量模型", "biz_name": "traffic_model", "tableQuery": "traffic_daily"},
            {"id": 11, "name": "客户模型", "biz_name": "customer_model", "tableQuery": "customer_daily"},
        ],
        metrics=[metric],
        dimensions=[other_model_time],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [SchemaElementMatch(element=metric, similarity=0.96, detect_word="访问人数", word="访问人数")]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今天店铺的访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今天店铺的访问人数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["访问人数"],
                    "time_mentions": ["今天"],
                    "time_range": {"raw": "今天", "value_status": "provided"},
                    "required_slot_types": ["metric", "time_range"],
                },
            },
        }
    )

    assert result["hit"] is False
    assert result["status"] == "missed"
    assert result["selected_assets"]["dimensions"] == []
    assert result["slot_bindings"]["filters"] == []
    assert result["decision"]["reason_code"] == "TIME_RANGE_PROVIDED_BUT_TIME_DIMENSION_MISSING"
    assert result["decision"]["missing_required_slots"] == ["time_dimension"]


def test_headless_knowledge_adapter_reranks_exact_metric_mention_above_partial_people_metrics():
    metrics = [
        SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            model=10,
            id=100,
            name="转化人数",
            biz_name="convert_uv",
            type="METRIC",
            fields=["convert_uv"],
        ),
        SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            model=10,
            id=101,
            name="关注人数",
            biz_name="follow_uv",
            type="METRIC",
            fields=["follow_uv"],
        ),
        SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            model=10,
            id=102,
            name="访问人数",
            biz_name="visit_uv",
            type="METRIC",
            alias=["访客数"],
            fields=["visit_uv"],
        ),
    ]
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "流量模型", "biz_name": "traffic_model", "tableQuery": "traffic_daily"}],
        metrics=metrics,
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper([]),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今日店铺的访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今日店铺的访问人数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["访问人数"],
                    "time_mentions": ["今日"],
                    "required_slot_types": ["metric", "time_range"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["status"] == "hit"
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 102
    assert result["candidate_groups"]["metrics"][0]["asset_id"] == 102
    assert "完整命中 metric mention: 访问人数" in result["candidate_groups"]["metrics"][0]["rerank_reason"]


def test_headless_knowledge_adapter_keeps_weak_people_metric_mention_ambiguous():
    first_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="转化人数",
        biz_name="convert_uv",
        type="METRIC",
        fields=["convert_uv"],
    )
    second_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=101,
        name="关注人数",
        biz_name="follow_uv",
        type="METRIC",
        fields=["follow_uv"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "流量模型", "biz_name": "traffic_model", "tableQuery": "traffic_daily"}],
        metrics=[first_metric, second_metric],
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper([]),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今日人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {
                "rewrite": {"rewritten_question": "今日人数"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["人数"],
                    "required_slot_types": ["metric"],
                },
            },
        }
    )

    assert result["hit"] is True
    assert result["status"] == "metric_ambiguous"
    assert result["selected_assets"]["metrics"] == []
    assert [item["asset_id"] for item in result["ambiguities"][0]["candidates"]] == [100, 101]


def test_headless_knowledge_adapter_routes_close_metric_candidates_to_ambiguity():
    first_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="销售额度",
        biz_name="sales_amount",
        type="METRIC",
        alias=["额度"],
        fields=["sales_amount"],
    )
    second_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=101,
        name="订单额度",
        biz_name="order_amount",
        type="METRIC",
        alias=["额度"],
        fields=["order_amount"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "交易模型", "biz_name": "trade_model", "tableQuery": "trade_daily"}],
        metrics=[first_metric, second_metric],
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper(
            [
                SchemaElementMatch(element=first_metric, similarity=0.82, detect_word="额度", word="额度"),
                SchemaElementMatch(element=second_metric, similarity=0.78, detect_word="额度", word="额度"),
            ]
        ),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今日额度", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {"rewrite": {"rewritten_question": "今日额度"}},
        }
    )

    assert result["hit"] is True
    assert result["status"] == "metric_ambiguous"
    assert result["selected_assets"]["metrics"] == []
    assert result["decision"]["status"] == "ambiguous"
    assert result["ambiguities"][0]["type"] == "metric"
    assert [item["asset_id"] for item in result["ambiguities"][0]["candidates"]] == [100, 101]


def test_headless_knowledge_adapter_retrieves_metric_from_asset_document_search_text():
    metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="访问量",
        biz_name="visit_uv",
        type="METRIC",
        description="店铺流量核心指标",
        default_agg="SUM",
        fields=["visit_uv"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "流量模型", "biz_name": "traffic_model", "tableQuery": "traffic_daily"}],
        metrics=[metric],
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper([]),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今日店铺流量", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {"rewrite": {"rewritten_question": "今日店铺流量"}},
        }
    )

    assert result["hit"] is True
    assert result["status"] == "hit"
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert result["candidate_groups"]["metrics"][0]["source"] == "headless_asset_document"
    assert result["candidate_groups"]["metrics"][0]["matched_field"] == "business_text"
    assert result["slot_bindings"]["metrics"][0]["source"] == "headless_asset_document"


def test_headless_knowledge_adapter_ranks_document_by_business_term_overlap():
    traffic_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="访问量",
        biz_name="visit_uv",
        type="METRIC",
        description="店铺流量 访问人数 核心指标",
        default_agg="SUM",
        fields=["visit_uv"],
    )
    trade_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=101,
        name="成交订单数",
        biz_name="order_cnt",
        type="METRIC",
        description="店铺交易 订单成交 指标",
        default_agg="SUM",
        fields=["order_cnt"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "经营模型", "biz_name": "business_model", "tableQuery": "business_daily"}],
        metrics=[traffic_metric, trade_metric],
        dimensions=[],
    )
    adapter = HeadlessKnowledgeAdapter(
        schema_builder=FakeHeadlessSchemaBuilder(schema),
        schema_mapper=FakeSchemaMapper([]),
    )

    result = adapter.retrieve(
        {
            "request": {"question": "今日店铺流量", "dataset_id": 20, "tenant_id": 10, "user_id": 20},
            "variables": {"rewrite": {"rewritten_question": "今日店铺流量"}},
        }
    )

    assert result["hit"] is True
    assert result["status"] == "hit"
    assert result["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert result["selected_assets"]["metrics"][0]["score"] - result["candidate_groups"]["metrics"][1]["score"] >= 0.12


def test_candidate_groups_output_strips_full_payload():
    """输出边界瘦身：candidate_groups 不携带资产全量 payload，且不影响原对象。"""

    groups = {
        "metrics": [{"asset_id": 1, "name": "销售额", "score": 0.9, "payload": {"ext_info": {"big": "blob"}}}],
        "dimensions": [],
    }

    public = HeadlessKnowledgeAdapter._public_candidate_groups(groups)

    assert public["metrics"][0] == {"asset_id": 1, "name": "销售额", "score": 0.9}
    # ambiguities/selected_assets 仍引用原对象，payload 必须保留在原件上。
    assert "payload" in groups["metrics"][0]
