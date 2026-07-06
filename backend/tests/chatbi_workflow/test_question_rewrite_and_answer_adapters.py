import json
import threading
import time

from apps.chatbi_workflow.capabilities.adapters.answer import (
    AnswerAdapter,
    build_answer_generation_prompt,
    build_answer_projection,
)
from apps.chatbi_workflow.capabilities.adapters.question import (
    IntentSubtaskConfig,
    QuestionAdapter,
    build_dimension_slots_prompt,
    build_intent_recognition_prompt,
    build_intent_shape_prompt,
    build_question_rewrite_prompt,
    build_semantic_mentions_prompt,
)
from apps.chatbi_workflow.capabilities.adapters.recommendation import (
    RecommendationAdapter,
)
from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi_workflow.capabilities.real import RealChatBICapabilityGateway
from apps.headless.schemas import DataSetSchema, SchemaElement


class FakeModelClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class SequenceModelClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return self.responses[index]


class PromptAwareConcurrentModelClient:
    def __init__(self, responses_by_marker: dict[str, str], delays_by_marker: dict[str, float] | None = None) -> None:
        self.responses_by_marker = responses_by_marker
        self.delays_by_marker = delays_by_marker or {}
        self.prompts = []
        self.thread_names = []
        self.started_markers = []
        self.finished_markers = []
        self._lock = threading.Lock()

    def __call__(self, prompt):
        marker = self._marker_for_prompt(prompt)
        with self._lock:
            self.prompts.append(prompt)
            self.thread_names.append(threading.current_thread().name)
            self.started_markers.append(marker)
        delay = self.delays_by_marker.get(marker, 0)
        if delay:
            time.sleep(delay)
        with self._lock:
            self.finished_markers.append(marker)
        return self.responses_by_marker[marker]

    @staticmethod
    def _marker_for_prompt(prompt) -> str:
        if "分析形态识别器" in prompt.system_prompt:
            return "shape"
        if "指标和时间线索识别器" in prompt.system_prompt or "指标线索和时间线索识别器" in prompt.system_prompt:
            return "semantic"
        if "维度槽位识别器" in prompt.system_prompt:
            return "dimensions"
        raise AssertionError(f"unexpected prompt: {prompt.system_prompt}")


class FailingPromptAwareModelClient(PromptAwareConcurrentModelClient):
    def __init__(self, responses_by_marker: dict[str, str], failing_marker: str) -> None:
        super().__init__(responses_by_marker=responses_by_marker)
        self.failing_marker = failing_marker

    def __call__(self, prompt):
        marker = self._marker_for_prompt(prompt)
        if marker == self.failing_marker:
            with self._lock:
                self.prompts.append(prompt)
                self.thread_names.append(threading.current_thread().name)
                self.started_markers.append(marker)
                self.finished_markers.append(marker)
            raise RuntimeError(f"{marker} failed")
        return super().__call__(prompt)


class FakeSqlAdapter:
    def generate(self, request):
        return {
            "sql": "select sum(visit_uv) as visit_uv from stall_traffic_1d",
            "strategy": "semantic_sql_compiler",
            "explanation": "fake sql",
            "used_assets": [],
        }


class FakeHeadlessSchemaBuilder:
    def __init__(self, schema: DataSetSchema) -> None:
        self.schema = schema
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        self.calls.append((oid, dataset_id))
        return self.schema


DEFAULT_SUBJECT_DOMAIN = {
    "status": "not_required",
    "domain_id": None,
    "domain_name": None,
    "domain_biz_name": None,
    "confidence": 0.0,
    "reason": "",
    "candidate_domain_ids": [],
}


DEFAULT_VALIDATION = {
    "status": "valid",
    "reason_code": "INTENT_VALID",
    "repair_hint": None,
    "retryable": False,
    "retry_count": 0,
    "max_retry_count": 2,
    "violations": [],
    "clarification_required": False,
    "slot_issues": [],
}


def _v1_request(question: str, variables: dict | None = None, conversation: dict | None = None) -> dict:
    return {
        "request": {"question": question, "tenant_id": 9501, "user_id": 501, "dataset_id": 7001},
        "conversation": conversation or {},
        "variables": variables or {},
        "inputs": {},
        "node_name": "rewrite_question",
    }


def test_question_rewrite_prompt_constrains_model_to_rewrite_only():
    prompt = build_question_rewrite_prompt(
        question="那上个月呢",
        conversation_context={"last_question": "这个月销售额"},
        user_feedback={},
    )

    assert "只做问题重写" in prompt.system_prompt
    assert "不要回答问题" in prompt.system_prompt
    assert "不要生成 SQL" in prompt.system_prompt
    assert '"rewritten_question"' in prompt.system_prompt
    assert '"need_user_input"' in prompt.system_prompt
    assert "那上个月呢" in prompt.user_prompt
    assert "last_question" in prompt.user_prompt


def test_question_rewrite_prompt_defines_chatbi_required_information():
    prompt = build_question_rewrite_prompt(
        question="看一下情况",
        conversation_context={},
        user_feedback={},
    )

    assert "ChatBI 必需信息判定" in prompt.system_prompt
    assert "dataset_id" in prompt.system_prompt
    assert "metric" in prompt.system_prompt
    assert "analysis_object" in prompt.system_prompt
    assert "time_range" in prompt.system_prompt
    assert "dimension" in prompt.system_prompt
    assert "filter" in prompt.system_prompt
    assert "默认不要因为缺少时间范围而澄清" in prompt.system_prompt
    assert "只有用户明确要求趋势、对比、环比、同比、排行、按维度拆解" in prompt.system_prompt


def test_question_adapter_rewrites_question_with_model_json():
    adapter = QuestionAdapter(
        model_client=FakeModelClient(
            '{"rewritten_question":"上个月销售额是多少","need_user_input":false,'
            '"missing_slots":[],"image_profile_hint":"table"}'
        )
    )

    result = adapter.rewrite(_v1_request("那上个月呢", conversation={"last_question": "这个月销售额是多少"}))

    assert result == {
        "rewritten_question": "上个月销售额是多少",
        "need_user_input": False,
        "missing_slots": [],
        "image_profile_hint": "table",
    }


def test_question_adapter_rewrite_degrades_to_original_question_when_model_output_is_invalid():
    adapter = QuestionAdapter(model_client=FakeModelClient("不是 JSON"))

    result = adapter.rewrite(_v1_request("今日的访问量"))

    assert result == {
        "rewritten_question": "今日的访问量",
        "need_user_input": False,
        "missing_slots": [],
        "image_profile_hint": None,
    }


def test_question_adapter_rewrite_fallback_can_still_request_clarification():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.rewrite(_v1_request("需要澄清的问题"))

    assert result == {
        "rewritten_question": "需要澄清的问题",
        "need_user_input": True,
        "missing_slots": ["metric"],
        "image_profile_hint": None,
    }


def test_intent_recognition_prompt_constrains_model_to_structured_intent():
    prompt = build_intent_recognition_prompt(
        rewritten_question="近 30 天访问量趋势",
        conversation_context={},
        user_feedback={},
    )

    assert "只做意图识别" in prompt.system_prompt
    assert "不要回答问题" in prompt.system_prompt
    assert "不要生成 SQL" in prompt.system_prompt
    assert '"intent_type"' in prompt.system_prompt
    assert '"confidence"' in prompt.system_prompt
    assert "metric_query" in prompt.system_prompt
    assert "trend_analysis" in prompt.system_prompt
    assert "ranking_analysis" in prompt.system_prompt
    assert "metric_mentions" in prompt.system_prompt
    assert "dimension_mentions" in prompt.system_prompt
    assert "dimension_slots" in prompt.system_prompt
    assert "time_mentions" in prompt.system_prompt
    assert "time_range" in prompt.system_prompt
    assert "普通维度槽位的 value 不得是时间表达" in prompt.system_prompt
    assert "今天/昨天/本月/最近7天" in prompt.system_prompt
    assert "required_slot_types" in prompt.system_prompt
    assert "query_shape" in prompt.system_prompt
    assert "不能选择真实指标、维度或枚举值 ID" in prompt.system_prompt
    assert "ambiguous_slots" in prompt.system_prompt
    assert "conflict_slots" in prompt.system_prompt
    assert "近 30 天访问量趋势" in prompt.user_prompt


def test_intent_recognition_prompt_includes_subject_domain_candidates():
    prompt = build_intent_recognition_prompt(
        rewritten_question="今天档口 1 的访问人数",
        conversation_context={},
        user_feedback={},
        subject_domains=[
            {
                "domain_id": 1,
                "name": "店铺",
                "biz_name": "shop",
                "description": "店铺、档口、门店经营分析",
                "model_ids": [10],
            },
            {
                "domain_id": 2,
                "name": "商品",
                "biz_name": "product",
                "description": "商品销售、库存和价格分析",
                "model_ids": [11],
            },
        ],
    )

    assert "subject_domain" in prompt.system_prompt
    assert "只能从候选主题域中选择" in prompt.system_prompt
    assert "店铺、档口、门店经营分析" in prompt.user_prompt
    assert "商品销售、库存和价格分析" in prompt.user_prompt


def test_intent_recognition_prompt_includes_available_dimension_candidates():
    prompt = build_dimension_slots_prompt(
        rewritten_question="今天店铺1的线上客户数",
        available_dimensions=[
            {
                "name": "店铺ID",
                "aliases": ["店铺", "档口"],
                "data_type": "bigint",
                "semantic_type": "identifier",
                "value_kind": "numeric_id",
                "is_time": False,
            },
            {"name": "时间", "aliases": ["日期", "统计日期"], "data_type": "date", "value_kind": "date", "is_time": True},
        ],
    )

    assert "维度槽位识别器" in prompt.system_prompt
    assert "dimension_slots[].name" in prompt.system_prompt
    assert "residual_filter_mentions" in prompt.system_prompt
    assert "# 可用维度" in prompt.user_prompt
    assert "店铺ID" in prompt.user_prompt
    assert "numeric_id" in prompt.user_prompt
    assert "统计日期" in prompt.user_prompt


def test_dimension_slots_prompt_separates_time_dimensions_from_plain_dimensions():
    prompt = build_dimension_slots_prompt(
        rewritten_question="今天店铺1的客户数",
        available_dimensions=[
            {"name": "店铺ID", "aliases": ["店铺", "档口"], "value_kind": "numeric_id", "is_time": False},
            {"name": "时间", "aliases": ["日期", "统计日期"], "value_kind": "date", "is_time": True},
        ],
    )

    assert "普通维度候选" in prompt.system_prompt
    assert "时间字段候选" in prompt.system_prompt
    assert "# 时间字段候选" in prompt.user_prompt
    plain_section = prompt.user_prompt.split("# 可用维度", 1)[1].split("# 时间字段候选", 1)[0]
    time_section = prompt.user_prompt.split("# 时间字段候选", 1)[1].split("# 会话上下文", 1)[0]
    assert "店铺ID" in plain_section
    assert "时间" not in plain_section
    assert "时间" in time_section
    assert "统计日期" in time_section


def test_dimension_slots_prompt_guides_semantic_mapping_to_available_dimensions():
    prompt = build_dimension_slots_prompt(
        "最近 30 天商城店铺的总 GMV 是多少？",
        available_dimensions=[
            {"name": "档口ID", "aliases": ["档口"]},
            {"name": "商家ID", "aliases": ["商家"]},
        ],
    )

    assert "语义最相近" in prompt.system_prompt
    assert "输出对应的标准 `name`" in prompt.system_prompt
    assert "多个候选都可能匹配" in prompt.system_prompt
    assert "ambiguous_slots" in prompt.system_prompt


def test_split_intent_prompts_are_scoped_to_small_outputs():
    shape_prompt = build_intent_shape_prompt("最近7天销售额趋势")
    semantic_prompt = build_semantic_mentions_prompt("最近7天销售额趋势")
    dimension_prompt = build_dimension_slots_prompt(
        "店铺1销售额",
        available_dimensions=[{"name": "店铺ID", "aliases": ["店铺"], "value_kind": "numeric_id"}],
    )

    assert "分析形态识别器" in shape_prompt.system_prompt
    assert "metric_mentions" not in shape_prompt.system_prompt
    assert "dimension_slots" not in shape_prompt.system_prompt
    assert "指标线索和时间线索识别器" in semantic_prompt.system_prompt
    assert "dimension_slots" not in semantic_prompt.system_prompt
    assert "维度槽位识别器" in dimension_prompt.system_prompt
    assert "metric_mentions" not in dimension_prompt.system_prompt
    assert "# 可用维度" in dimension_prompt.user_prompt


def test_question_adapter_recognizes_intent_with_split_subtasks_and_program_merge():
    adapter = QuestionAdapter(
        model_client=SequenceModelClient(
            [
                '{"intent_type":"trend_analysis","confidence":0.93,'
                '"required_slot_types":["metric","time_dimension"],'
                '"query_shape":{"select_mode":"aggregate","needs_group_by":true,"time_grain":"day"},'
                '"subject_domain":{"status":"not_required"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"metric_mentions":["访问人数"],"time_mentions":["近 30 天"],'
                '"time_range":{"raw":"近 30 天","value_status":"provided"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":["档口"],'
                '"dimension_slots":[{"name":"档口","role":"group_by","value":null,"value_status":"not_provided"}],'
                '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
            ]
        )
    )

    result = adapter.recognize_intent(
        _v1_request("近 30 天访问量趋势", variables={"rewrite": {"rewritten_question": "近 30 天访问量趋势"}})
    )

    assert [prompt.system_prompt.splitlines()[0] for prompt in adapter._model_client.prompts] == [
        "# 角色",
        "# 角色",
        "# 角色",
    ]
    assert "分析形态识别器" in adapter._model_client.prompts[0].system_prompt
    assert "指标线索和时间线索识别器" in adapter._model_client.prompts[1].system_prompt
    assert "维度槽位识别器" in adapter._model_client.prompts[2].system_prompt
    assert result == {
        "intent_type": "trend_analysis",
        "confidence": 0.93,
        "metric_mentions": ["访问人数"],
        "dimension_mentions": ["档口"],
        "dimension_slots": [{"name": "档口", "role": "group_by", "value": None, "value_status": "not_provided"}],
        "time_mentions": ["近 30 天"],
        "time_range": {
            "raw": "近 30 天",
            "value_status": "provided",
            "normalized": {
                "kind": "relative_range",
                "unit": "day",
                "amount": 30,
                "anchor": "today",
                "include_current": True,
                "timezone": "Asia/Shanghai",
            },
        },
        "filter_mentions": [],
        "required_slot_types": ["metric", "time_dimension"],
        "query_shape": {"select_mode": "aggregate", "needs_group_by": True, "time_grain": "day"},
        "subject_domain": DEFAULT_SUBJECT_DOMAIN,
        "ambiguous_slots": [],
        "conflict_slots": [],
        "validation": DEFAULT_VALIDATION,
    }


def test_question_adapter_parallel_intent_subtasks_merge_by_name_not_finish_order():
    model_client = PromptAwareConcurrentModelClient(
        responses_by_marker={
            "shape": json.dumps(
                {
                    "intent_type": "trend_analysis",
                    "confidence": 0.91,
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate", "time_grain": "day"},
                    "subject_domain": {"status": "not_required"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "semantic": json.dumps(
                {
                    "metric_mentions": ["访问人数"],
                    "time_mentions": ["最近 7 天"],
                    "time_range": {"raw": "最近 7 天", "value_status": "provided"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "dimensions": json.dumps(
                {
                    "dimension_mentions": ["店铺"],
                    "dimension_slots": [
                        {"name": "店铺", "role": "group_by", "value": None, "value_status": "not_provided"}
                    ],
                    "residual_filter_mentions": [],
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
        },
        delays_by_marker={"shape": 0.08, "semantic": 0.04, "dimensions": 0.0},
    )
    adapter = QuestionAdapter(model_client=model_client)

    result = adapter.recognize_intent(
        _v1_request("最近 7 天按店铺看访问人数", variables={"rewrite": {"rewritten_question": "最近 7 天按店铺看访问人数"}})
    )

    assert result["intent_type"] == "trend_analysis"
    assert result["metric_mentions"] == ["访问人数"]
    assert result["dimension_mentions"] == ["店铺"]
    assert result["query_shape"] == {"select_mode": "aggregate", "time_grain": "day"}
    assert set(model_client.started_markers) == {"shape", "semantic", "dimensions"}
    assert model_client.finished_markers[0] == "dimensions"
    assert adapter.last_intent_subtask_trace["enabled"] is True
    assert adapter.last_intent_subtask_trace["all_subtasks_fallback"] is False
    assert set(adapter.last_intent_subtask_trace["subtasks"]) == {"shape", "semantic", "dimensions"}


def test_question_adapter_parallel_intent_subtask_failure_only_falls_back_that_subtask():
    model_client = FailingPromptAwareModelClient(
        responses_by_marker={
            "shape": json.dumps(
                {
                    "intent_type": "ranking_analysis",
                    "confidence": 0.92,
                    "required_slot_types": ["metric", "dimension", "order", "limit"],
                    "query_shape": {"select_mode": "aggregate", "needs_order_by": True, "order_direction": "desc"},
                    "subject_domain": {"status": "not_required"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "semantic": "{}",
            "dimensions": json.dumps(
                {
                    "dimension_mentions": ["商品"],
                    "dimension_slots": [
                        {"name": "商品", "role": "group_by", "value": None, "value_status": "not_provided"}
                    ],
                    "residual_filter_mentions": [],
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
        },
        failing_marker="semantic",
    )
    adapter = QuestionAdapter(model_client=model_client)

    result = adapter.recognize_intent(
        _v1_request("销售额最高的商品", variables={"rewrite": {"rewritten_question": "销售额最高的商品"}})
    )

    assert result["intent_type"] == "ranking_analysis"
    assert result["metric_mentions"] == ["销售额"]
    assert result["dimension_mentions"] == ["商品"]
    assert adapter.last_intent_subtask_trace["subtasks"]["semantic"]["status"] == "fallback"
    assert adapter.last_intent_subtask_trace["subtasks"]["semantic"]["source"] == "exception_fallback"
    assert adapter.last_intent_subtask_trace["subtasks"]["shape"]["status"] == "succeeded"
    assert adapter.last_intent_subtask_trace["subtasks"]["dimensions"]["status"] == "succeeded"


def test_question_adapter_parallel_intent_records_all_subtasks_fallback_when_model_unavailable():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.recognize_intent(
        _v1_request("看一下销售额", variables={"rewrite": {"rewritten_question": "看一下销售额"}})
    )

    assert result["metric_mentions"] == ["销售额"]
    assert adapter.last_intent_subtask_trace["all_subtasks_fallback"] is True
    assert {name: item["source"] for name, item in adapter.last_intent_subtask_trace["subtasks"].items()} == {
        "shape": "exception_fallback",
        "semantic": "exception_fallback",
        "dimensions": "exception_fallback",
    }


def test_question_adapter_can_disable_parallel_intent_subtasks():
    model_client = PromptAwareConcurrentModelClient(
        responses_by_marker={
            "shape": json.dumps(
                {
                    "intent_type": "metric_query",
                    "confidence": 0.9,
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate"},
                    "subject_domain": {"status": "not_required"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "semantic": json.dumps(
                {
                    "metric_mentions": ["访问人数"],
                    "time_mentions": [],
                    "time_range": {"raw": None, "value_status": "not_provided"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "dimensions": json.dumps(
                {
                    "dimension_mentions": [],
                    "dimension_slots": [],
                    "residual_filter_mentions": [],
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
        }
    )
    adapter = QuestionAdapter(
        model_client=model_client,
        intent_subtask_config=IntentSubtaskConfig(enabled=False),
    )

    result = adapter.recognize_intent(_v1_request("访问人数", variables={"rewrite": {"rewritten_question": "访问人数"}}))

    assert result["metric_mentions"] == ["访问人数"]
    assert model_client.started_markers == ["shape", "semantic", "dimensions"]
    assert adapter.last_intent_subtask_trace["enabled"] is False


def test_question_adapter_parallel_intent_subtask_timeout_uses_fallback_payload():
    model_client = PromptAwareConcurrentModelClient(
        responses_by_marker={
            "shape": json.dumps(
                {
                    "intent_type": "metric_query",
                    "confidence": 0.9,
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate"},
                    "subject_domain": {"status": "not_required"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "semantic": json.dumps(
                {
                    "metric_mentions": ["模型不应返回"],
                    "time_mentions": [],
                    "time_range": {"raw": None, "value_status": "not_provided"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
            "dimensions": json.dumps(
                {
                    "dimension_mentions": [],
                    "dimension_slots": [],
                    "residual_filter_mentions": [],
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            ),
        },
        delays_by_marker={"semantic": 0.2},
    )
    adapter = QuestionAdapter(
        model_client=model_client,
        intent_subtask_config=IntentSubtaskConfig(
            enabled=True,
            max_workers=3,
            subtask_timeout_seconds=0.05,
            overall_timeout_seconds=0.05,
        ),
    )

    result = adapter.recognize_intent(_v1_request("销售额", variables={"rewrite": {"rewritten_question": "销售额"}}))

    assert result["metric_mentions"] == ["销售额"]
    assert adapter.last_intent_subtask_trace["subtasks"]["semantic"]["source"] == "timeout_fallback"


def test_question_adapter_recognizes_subject_domain_from_dataset_schema():
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=7001,
            data_set_name="经营分析",
            id=7001,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        subject_domains=[
            {"domain_id": 1, "name": "店铺", "biz_name": "shop", "description": "店铺和档口主题", "model_ids": [10]},
            {"domain_id": 2, "name": "商品", "biz_name": "product", "description": "商品经营主题", "model_ids": [11]},
        ],
    )
    schema_builder = FakeHeadlessSchemaBuilder(schema)
    model_client = SequenceModelClient(
        [
            '{"intent_type":"metric_query","confidence":0.91,'
            '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
            '"subject_domain":{"status":"selected","domain_id":2,"confidence":0.91,"reason":"命中商品主题"},'
            '"ambiguous_slots":[],"conflict_slots":[]}',
            '{"metric_mentions":["访问人数"],"time_mentions":["今天"],'
            '"time_range":{"raw":"今天","value_status":"provided"},"ambiguous_slots":[],"conflict_slots":[]}',
            '{"dimension_mentions":[],"dimension_slots":[],"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
        ]
    )
    adapter = QuestionAdapter(model_client=model_client, schema_builder=schema_builder)

    result = adapter.recognize_intent(
        _v1_request("今天商品访问人数", variables={"rewrite": {"rewritten_question": "今天商品访问人数"}})
    )

    assert schema_builder.calls == [(9501, 7001)]
    assert "商品经营主题" in model_client.prompts[0].user_prompt
    assert result["subject_domain"] == {
        "status": "selected",
        "domain_id": 2,
        "domain_name": "商品",
        "domain_biz_name": "product",
        "confidence": 0.91,
        "reason": "命中商品主题",
        "candidate_domain_ids": [2],
    }


def test_question_adapter_intent_falls_back_to_rules_when_model_output_is_invalid():
    adapter = QuestionAdapter(model_client=FakeModelClient("不是 JSON"))

    result = adapter.recognize_intent(
        _v1_request("销售额最高的商品", variables={"rewrite": {"rewritten_question": "销售额最高的商品"}})
    )

    assert result == {
        "intent_type": "ranking_analysis",
        "confidence": 0.85,
        "metric_mentions": ["销售额"],
        "dimension_mentions": ["商品"],
        "dimension_slots": [{"name": "商品", "role": "group_by", "value": None, "value_status": "not_provided"}],
        "time_mentions": [],
        "time_range": {"raw": None, "value_status": "not_provided"},
        "filter_mentions": [],
        "required_slot_types": ["metric", "dimension", "order", "limit"],
        "query_shape": {
            "select_mode": "aggregate",
            "needs_group_by": True,
            "needs_order_by": True,
            "order_direction": "desc",
            "limit": None,
        },
        "subject_domain": DEFAULT_SUBJECT_DOMAIN,
        "ambiguous_slots": [],
        "conflict_slots": [],
        "validation": DEFAULT_VALIDATION,
    }


def test_question_adapter_intent_marks_ambiguous_metric_when_question_is_too_vague():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.recognize_intent(_v1_request("看一下情况", variables={"rewrite": {"rewritten_question": "看一下情况"}}))

    assert result == {
        "intent_type": "unknown",
        "confidence": 0.4,
        "metric_mentions": [],
        "dimension_mentions": [],
        "dimension_slots": [],
        "time_mentions": [],
        "time_range": {"raw": None, "value_status": "not_provided"},
        "filter_mentions": [],
        "required_slot_types": ["metric"],
        "query_shape": {"select_mode": "unknown"},
        "subject_domain": DEFAULT_SUBJECT_DOMAIN,
        "ambiguous_slots": ["metric"],
        "conflict_slots": [],
        "validation": DEFAULT_VALIDATION,
    }


def test_question_adapter_intent_fallback_extracts_dimension_slot_and_time_range():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.recognize_intent(
        _v1_request("今天档口的访问人数", variables={"rewrite": {"rewritten_question": "今天档口的访问人数"}})
    )

    assert result["intent_type"] == "metric_query"
    assert result["metric_mentions"] == ["访问人数"]
    assert result["dimension_mentions"] == ["档口"]
    assert result["dimension_slots"] == [
        {"name": "档口", "role": "ambiguous", "value": None, "value_status": "not_provided"}
    ]
    assert result["time_mentions"] == ["今天"]
    assert result["time_range"] == {
        "raw": "今天",
        "value_status": "provided",
        "normalized": {
            "kind": "single_date",
            "anchor": "today",
            "offset_days": 0,
            "timezone": "Asia/Shanghai",
        },
    }


def test_question_adapter_intent_fallback_extracts_explicit_month_and_topn():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.recognize_intent(
        _v1_request(
            "2026 年 6 月销售 GMV 最高的 5 个档口是哪些？",
            variables={"rewrite": {"rewritten_question": "2026 年 6 月销售 GMV 最高的 5 个档口是哪些？"}},
        )
    )

    assert result["intent_type"] == "ranking_analysis"
    assert result["metric_mentions"] == ["GMV"]
    assert result["dimension_slots"] == [
        {"name": "档口", "role": "group_by", "value": None, "value_status": "not_provided"}
    ]
    assert result["time_mentions"] == ["2026 年 6 月"]
    assert result["time_range"]["normalized"] == {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }
    assert result["query_shape"]["order_direction"] == "desc"
    assert result["query_shape"]["limit"] == 5


def test_question_adapter_intent_fallback_keeps_multiple_explicit_metrics():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    result = adapter.recognize_intent(
        _v1_request(
            "最近 30 天每天的总订单数和总 GMV 趋势如何？",
            variables={"rewrite": {"rewritten_question": "最近 30 天每天的总订单数和总 GMV 趋势如何？"}},
        )
    )

    assert result["metric_mentions"] == ["订单数", "GMV"]


def test_question_adapter_applies_confirmed_intent_feedback_deterministically():
    adapter = QuestionAdapter(
        model_client=FakeModelClient(
            '{"intent_type":"unknown","confidence":0.5,'
            '"metric_mentions":["访问人数"],"dimension_mentions":["店铺"],'
            '"dimension_slots":[{"name":"店铺","role":"ambiguous","value":null,"value_status":"not_provided"}],'
            '"time_mentions":["今天"],"time_range":{"raw":"今天","value_status":"provided"},'
            '"ambiguous_slots":["intent_type","dimension"],"conflict_slots":[]}'
        )
    )

    result = adapter.recognize_intent(
        _v1_request(
            "今天店铺的访问人数",
            variables={
                "rewrite": {"rewritten_question": "今天店铺的访问人数"},
                "intent_response": {"intent": "metric_query"},
            },
        )
    )

    assert result["intent_type"] == "metric_query"
    assert result["confidence"] == 0.95
    assert result["ambiguous_slots"] == ["dimension"]
    assert result["metric_mentions"] == ["访问人数"]
    assert result["dimension_mentions"] == ["店铺"]


def test_question_adapter_retries_dimension_subtask_when_dimension_value_is_time_expression():
    model_client = SequenceModelClient(
        [
            '{"intent_type":"metric_query","confidence":0.95,'
            '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
            '"subject_domain":{"status":"not_required"},'
            '"ambiguous_slots":[],"conflict_slots":[]}',
            '{"metric_mentions":["访问人数"],"time_mentions":["今天"],'
            '"time_range":{"raw":"今天","value_status":"provided"},'
            '"ambiguous_slots":[],"conflict_slots":[]}',
            '{"dimension_mentions":["店铺"],'
            '"dimension_slots":[{"name":"店铺","role":"filter","value":"今天","value_status":"provided"}],'
            '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
            '{"dimension_mentions":["店铺"],'
            '"dimension_slots":[{"name":"店铺","role":"ambiguous","value":null,"value_status":"not_provided"}],'
            '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
        ]
    )
    adapter = QuestionAdapter(model_client=model_client)

    result = adapter.recognize_intent(
        _v1_request("今天店铺的访问人数", variables={"rewrite": {"rewritten_question": "今天店铺的访问人数"}})
    )

    assert len(model_client.prompts) == 4
    assert "普通维度值不能是时间表达" in model_client.prompts[3].user_prompt
    assert result["dimension_slots"] == [
        {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
    ]
    assert result["validation"]["clarification_required"] is True
    assert result["validation"]["slot_issues"][0]["slot_type"] == "dimension_value"


def test_question_adapter_uses_dimension_subtask_for_structured_filter_slots():
    adapter = QuestionAdapter(
        model_client=SequenceModelClient(
            [
                '{"intent_type":"metric_query","confidence":0.95,'
                '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
                '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
                '{"metric_mentions":["线上客户数"],"time_mentions":["今天"],'
                '"time_range":{"raw":"今天","value_status":"provided"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":["店铺"],'
                '"dimension_slots":[{"name":"店铺","role":"filter","value":"1","value_status":"provided"}],'
                '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
            ]
        )
    )

    result = adapter.recognize_intent(
        _v1_request("今天店铺1的线上客户数", variables={"rewrite": {"rewritten_question": "今天店铺1的线上客户数"}})
    )

    assert result["dimension_slots"] == [{"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}]
    assert result["dimension_mentions"] == ["店铺"]
    assert result["filter_mentions"] == []


def test_question_adapter_keeps_residual_filter_mentions_without_duplicate_dimension_filters():
    adapter = QuestionAdapter(
        model_client=SequenceModelClient(
            [
                '{"intent_type":"metric_query","confidence":0.9,'
                '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
                '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
                '{"metric_mentions":["销售额"],"time_mentions":[],"time_range":{"raw":null,"value_status":"not_provided"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":["店铺"],'
                '"dimension_slots":[{"name":"店铺","role":"filter","value":"1","value_status":"provided"}],'
                '"residual_filter_mentions":[{"name":"高价值客户","value":"高价值客户","status":"ungrounded"}],'
                '"ambiguous_slots":[],"conflict_slots":[]}',
            ]
        )
    )

    result = adapter.recognize_intent(
        _v1_request("店铺1的高价值客户销售额", variables={"rewrite": {"rewritten_question": "店铺1的高价值客户销售额"}})
    )

    assert result["dimension_slots"] == [{"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}]
    assert result["filter_mentions"] == [{"name": "高价值客户", "value": "高价值客户", "status": "ungrounded"}]


def test_question_adapter_preserves_unmatched_dimension_slots_and_normalizes_matched_aliases():
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=7001,
            data_set_name="店铺经营分析",
            id=7001,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        dimensions=[
            SchemaElement(
                data_set_id=7001,
                data_set_name="店铺经营分析",
                model=10,
                id=200,
                name="店铺ID",
                biz_name="stall_id",
                type="DIMENSION",
                alias=["店铺", "档口"],
            ),
            SchemaElement(
                data_set_id=7001,
                data_set_name="店铺经营分析",
                model=10,
                id=201,
                name="时间",
                biz_name="stat_date",
                type="DIMENSION",
                alias=["日期", "统计日期"],
                ext_info={"is_default_time": True},
            ),
        ],
    )
    model_client = SequenceModelClient(
        [
            '{"intent_type":"metric_query","confidence":0.95,'
            '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
            '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
            '{"metric_mentions":["线上客户数"],"time_mentions":["今天"],'
            '"time_range":{"raw":"今天","value_status":"provided"},'
            '"ambiguous_slots":[],"conflict_slots":[]}',
            '{"dimension_mentions":["线上","店铺"],'
            '"dimension_slots":['
            '{"name":"线上","role":"ambiguous","value":null,"value_status":"not_provided"},'
            '{"name":"店铺","role":"filter","value":"1","value_status":"provided"}'
            '],'
            '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
        ]
    )
    adapter = QuestionAdapter(model_client=model_client, schema_builder=FakeHeadlessSchemaBuilder(schema))

    result = adapter.recognize_intent(
        _v1_request("今天店铺1的线上客户数", variables={"rewrite": {"rewritten_question": "今天店铺1的线上客户数"}})
    )

    assert "# 可用维度" in model_client.prompts[2].user_prompt
    assert "线上" in result["dimension_mentions"]
    assert {"name": "店铺ID", "role": "filter", "value": "1", "value_status": "provided"} in result["dimension_slots"]
    assert {"name": "线上", "role": "ambiguous", "value": None, "value_status": "not_provided"} in result[
        "dimension_slots"
    ]
    assert result["validation"]["clarification_required"] is True


def test_semantic_mentions_prompt_does_not_force_unmatched_dimension_words_into_metrics():
    prompt = build_semantic_mentions_prompt(
        "今天店铺的访问人数",
        available_dimensions=[
            {"name": "档口ID", "aliases": []},
            {"name": "商家ID", "aliases": []},
        ],
    )

    assert "应保留在 `metric_mentions` 中" not in prompt.system_prompt
    assert "不能进入 dimension_mentions 或 dimension_slots" not in prompt.system_prompt


def test_dimension_slots_normalization_preserves_unmatched_natural_language_mentions():
    payload = {
        "dimension_mentions": ["店铺"],
        "dimension_slots": [
            {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"},
        ],
        "residual_filter_mentions": [],
        "ambiguous_slots": [],
        "conflict_slots": [],
    }

    result = QuestionAdapter._normalize_dimension_slots_payload(
        payload,
        available_dimensions=[
            {"name": "档口ID", "aliases": []},
            {"name": "商家ID", "aliases": []},
        ],
    )

    assert result["dimension_mentions"] == ["店铺"]
    assert result["dimension_slots"] == [
        {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
    ]


def test_question_adapter_filters_time_dimensions_from_plain_dimension_slots():
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=7001,
            data_set_name="店铺经营分析",
            id=7001,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        dimensions=[
            SchemaElement(
                data_set_id=7001,
                data_set_name="店铺经营分析",
                model=10,
                id=200,
                name="店铺ID",
                biz_name="stall_id",
                type="DIMENSION",
                alias=["店铺", "档口"],
            ),
            SchemaElement(
                data_set_id=7001,
                data_set_name="店铺经营分析",
                model=10,
                id=201,
                name="时间",
                biz_name="stat_date",
                type="DIMENSION",
                alias=["日期", "统计日期"],
                ext_info={"is_default_time": True},
            ),
        ],
    )
    model_client = SequenceModelClient(
        [
            '{"intent_type":"metric_query","confidence":0.95,'
            '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
            '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
            '{"metric_mentions":["档口客户数"],"time_mentions":["今天"],'
            '"time_range":{"raw":"今天","value_status":"provided"},'
            '"ambiguous_slots":[],"conflict_slots":[]}',
            '{"dimension_mentions":["时间","店铺"],'
            '"dimension_slots":['
            '{"name":"时间","role":"ambiguous","value":null,"value_status":"ambiguous"},'
            '{"name":"店铺","role":"filter","value":"1","value_status":"provided"}'
            '],'
            '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
        ]
    )
    adapter = QuestionAdapter(model_client=model_client, schema_builder=FakeHeadlessSchemaBuilder(schema))

    result = adapter.recognize_intent(
        _v1_request("今天店铺1的档口客户数", variables={"rewrite": {"rewritten_question": "今天店铺1的档口客户数"}})
    )

    assert result["time_range"]["raw"] == "今天"
    assert "time_dimension" in result["required_slot_types"]
    assert result["dimension_mentions"] == ["店铺ID"]
    assert result["dimension_slots"] == [
        {"name": "店铺ID", "role": "filter", "value": "1", "value_status": "provided"}
    ]
    assert result["validation"]["clarification_required"] is False


def test_question_adapter_retries_dimension_subtask_when_value_contains_dimension_alias():
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=7001,
            data_set_name="店铺经营分析",
            id=7001,
            name="店铺经营分析",
            biz_name="stall_bi",
            type="DATASET",
        ),
        dimensions=[
            SchemaElement(
                data_set_id=7001,
                data_set_name="店铺经营分析",
                model=10,
                id=200,
                name="店铺ID",
                biz_name="stall_id",
                type="DIMENSION",
                alias=["店铺"],
            )
        ],
    )
    adapter = QuestionAdapter(
        model_client=SequenceModelClient(
            [
                '{"intent_type":"metric_query","confidence":0.95,'
                '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
                '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
                '{"metric_mentions":["累积线上总客户数"],"time_mentions":[],"time_range":{"raw":null,"value_status":"not_provided"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":["店铺ID"],'
                '"dimension_slots":[{"name":"店铺ID","role":"filter","value":"店铺1","value_status":"provided"}],'
                '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":["店铺ID"],'
                '"dimension_slots":[{"name":"店铺ID","role":"filter","value":"1","value_status":"provided"}],'
                '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}',
            ]
        ),
        schema_builder=FakeHeadlessSchemaBuilder(schema),
    )

    result = adapter.recognize_intent(
        _v1_request("店铺1的累积线上总客户数", variables={"rewrite": {"rewritten_question": "店铺1的累积线上总客户数"}})
    )

    assert len(adapter._model_client.prompts) == 4
    assert "维度值不能包含已命中的维度名或别名" in adapter._model_client.prompts[3].user_prompt
    assert result["dimension_slots"] == [
        {"name": "店铺ID", "role": "filter", "value": "1", "value_status": "provided"}
    ]


def test_answer_generation_prompt_constrains_model_to_answer_json():
    prompt = build_answer_generation_prompt(
        mode="generate",
        question="今日的访问量",
        projection={"execution": {"status": "succeeded", "row_count": 1}},
    )

    assert "只生成用户可读回复" in prompt.system_prompt
    assert "不要输出 Markdown 代码块" in prompt.system_prompt
    assert '"answer"' in prompt.system_prompt
    assert '"warnings"' in prompt.system_prompt
    assert "今日的访问量" in prompt.user_prompt
    assert '"execution"' in prompt.user_prompt


def test_answer_projection_excludes_sql_candidates_and_full_result_rows():
    projection = build_answer_projection(
        _v1_request(
            "今日的访问量",
            variables={
                "rewrite": {"rewritten_question": "今日访问量"},
                "plan": {
                    "status": "ready",
                    "metrics": [{"asset_id": 1, "display_name": "访问量"}],
                },
                "execution": {
                    "status": "succeeded",
                    "row_count": 2,
                    "queries": [
                        {
                            "query_id": "query-0",
                            "sql": "select secret from private_table",
                            "datasource_id": 5,
                        }
                    ],
                    "results": [
                        {
                            "query_id": "query-0",
                            "status": "succeeded",
                            "row_count": 2,
                            "fields": ["value"],
                            "sample_rows": [{"value": 1}],
                            "artifact_ref": {"artifact_id": "artifact-1"},
                        }
                    ],
                },
                "knowledge": {
                    "candidate_groups": {
                        "metrics": [{"payload": {"private": "private-payload"}}]
                    },
                    "selected_assets": {"metrics": [{"payload": {"secret": True}}]},
                    "slot_bindings": {"metrics": [{"asset_id": 1}]},
                    "decision": {"status": "accepted", "reason": "口径已确认"},
                },
            },
        )
    )
    serialized = json.dumps(projection, ensure_ascii=False)

    assert "select secret from private_table" not in serialized
    assert "candidate_groups" not in serialized
    assert "selected_assets" not in serialized
    assert "slot_bindings" not in serialized
    assert "private-payload" not in serialized
    assert projection["execution"]["results"][0]["sample_rows"] == [
        {"value": 1}
    ]


def test_answer_adapter_generates_answer_with_model_json():
    adapter = AnswerAdapter(
        model_client=FakeModelClient(
            '{"answer":"今日访问量为 1,234。","warnings":[],"render_type":"text","citations":[]}'
        )
    )

    result = adapter.generate(_v1_request("今日的访问量", variables={"sql_execution": {"row_count": 1}}))

    assert result == {
        "answer": "今日访问量为 1,234。",
        "warnings": [],
        "render_type": "text",
        "citations": [],
    }


def test_answer_adapter_degrades_when_model_output_is_invalid():
    adapter = AnswerAdapter(model_client=FakeModelClient("不是 JSON"))

    result = adapter.generate(_v1_request("今日的访问量"))

    assert result == {
        "answer": "暂时无法生成完整回答，请稍后重试。",
        "warnings": ["answer_generation_parse_failed"],
        "render_type": "text",
        "citations": [],
    }


def test_answer_adapter_composes_final_reply_locally():
    adapter = AnswerAdapter(model_client=FakeModelClient(RuntimeError("should not call model")))

    result = adapter.compose(
        _v1_request(
            "今日的访问量",
            variables={
                "answer": {"answer": "今日访问量为 1,234。", "warnings": [], "render_type": "text"},
                "recommendations": {"questions": ["查看昨日访问量"]},
                "image_profile": {"profile": "table", "chart_candidates": ["table"]},
            },
        )
    )

    assert result == {
        "final_answer": "今日访问量为 1,234。",
        "recommendations": ["查看昨日访问量"],
        "chart": {"profile": "table", "chart_candidates": ["table"]},
        "metadata": {"source": "real_chatbi_v1"},
    }


def test_recommendation_adapter_generates_contextual_questions_from_selected_assets():
    adapter = RecommendationAdapter()

    result = adapter.recommend(
        _v1_request(
            "今日访问人数",
            variables={
                "knowledge": {
                    "selected_assets": {
                        "metrics": [{"display_name": "访问人数", "biz_name": "visit_uv"}],
                        "dimensions": [{"display_name": "店铺", "biz_name": "stall_id"}],
                    },
                    "metrics": ["visit_uv"],
                    "dimensions": ["stall_id"],
                },
                "sql_execution": {"status": "succeeded", "row_count": 3},
            },
        )
    )

    assert result == {
        "questions": [
            "查看访问人数最近 7 天趋势",
            "按店铺对比访问人数",
            "查看访问人数较昨日变化",
        ]
    }


def test_real_gateway_routes_rewrite_and_answer_capabilities_to_real_adapters():
    question_adapter = QuestionAdapter(
        model_client=SequenceModelClient(
            [
                '{"rewritten_question":"今日访问量","need_user_input":false,'
                '"missing_slots":[],"image_profile_hint":"table"}',
                '{"intent_type":"metric_query","confidence":0.95,'
                '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
                '"subject_domain":{"status":"not_required"},"ambiguous_slots":[],"conflict_slots":[]}',
                '{"metric_mentions":["访问量"],"time_mentions":["今日"],'
                '"time_range":{"raw":"今日","value_status":"provided"},'
                '"ambiguous_slots":[],"conflict_slots":[]}',
                '{"dimension_mentions":[],"dimension_slots":[],"residual_filter_mentions":[],'
                '"ambiguous_slots":[],"conflict_slots":[]}',
            ]
        )
    )
    answer_adapter = AnswerAdapter(
        model_client=FakeModelClient(
            '{"answer":"今日访问量为 1,234。","warnings":[],"render_type":"text","citations":[]}'
        )
    )
    gateway = RealChatBICapabilityGateway(
        question_adapter=question_adapter,
        answer_adapter=answer_adapter,
        sql_adapter=FakeSqlAdapter(),
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )

    rewrite = gateway.invoke("question.rewrite", _v1_request("今日的访问量"), "run:rewrite")
    intent = gateway.invoke(
        "intent.recognize",
        _v1_request("今日的访问量", variables={"rewrite": {"rewritten_question": "今日访问量"}}),
        "run:intent",
    )
    answer = gateway.invoke("answer.generate", _v1_request("今日的访问量"), "run:answer")
    sql = gateway.invoke("sql.generate", _v1_request("今日的访问量"), "run:sql")
    recommendations = gateway.invoke(
        "question.recommend",
        _v1_request(
            "今日的访问量",
            variables={
                "knowledge": {
                    "selected_assets": {"metrics": [{"display_name": "访问人数", "biz_name": "visit_uv"}]},
                    "metrics": ["visit_uv"],
                }
            },
        ),
        "run:recommend",
    )

    assert rewrite["rewritten_question"] == "今日访问量"
    assert intent["intent_type"] == "metric_query"
    assert answer["answer"] == "今日访问量为 1,234。"
    assert sql["strategy"] == "semantic_sql_compiler"
    assert sql["sql"] == "select sum(visit_uv) as visit_uv from stall_traffic_1d"
    assert recommendations["questions"][0] == "查看访问人数最近 7 天趋势"
