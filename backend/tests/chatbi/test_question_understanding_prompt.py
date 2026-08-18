from apps.chatbi.orchestration.graph.capabilities.adapters.question import (
    build_dimension_slots_prompt,
    build_question_rewrite_prompt,
    build_semantic_mentions_prompt,
)
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.chatbi.services.understanding.understanding_service import (
    DIMENSION_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
)


def test_agent_and_graph_share_question_rewrite_business_rules():
    graph_prompt = build_question_rewrite_prompt("那上个月呢")

    assert QUESTION_REWRITE_BUSINESS_RULES in REWRITE_SYSTEM_PROMPT
    assert QUESTION_REWRITE_BUSINESS_RULES in graph_prompt.system_prompt


def test_agent_and_graph_share_metric_and_time_extraction_rules():
    graph_prompt = build_semantic_mentions_prompt("最近7天销售额")

    assert METRIC_TIME_EXTRACTION_RULES in INTENT_SYSTEM_PROMPT
    assert METRIC_TIME_EXTRACTION_RULES in graph_prompt.system_prompt


def test_agent_and_graph_share_dimension_extraction_rules():
    graph_prompt = build_dimension_slots_prompt("按城市看销售额")

    assert DIMENSION_EXTRACTION_RULES in DIMENSION_SYSTEM_PROMPT
    assert DIMENSION_EXTRACTION_RULES in graph_prompt.system_prompt
    assert "residual_filter_mentions" in DIMENSION_SYSTEM_PROMPT
    assert "不能输出字符串" in DIMENSION_SYSTEM_PROMPT
    assert "最高的5个门店" in DIMENSION_SYSTEM_PROMPT


def test_agent_intent_prompt_requires_model_generated_query_shape():
    assert "query_shape 必须完整输出全部字段" in INTENT_SYSTEM_PROMPT
    assert "没有明确数量时必须为 null" in INTENT_SYSTEM_PROMPT
    assert "time_grain 只填写用户明确表达" in INTENT_SYSTEM_PROMPT
    assert "不输出 dimension_mentions" in INTENT_SYSTEM_PROMPT
    assert "必需槽位由服务端派生" in INTENT_SYSTEM_PROMPT
    assert "不要求本阶段确定完整指标口径" in INTENT_SYSTEM_PROMPT


def test_metric_prompt_preserves_complete_business_phrase():
    assert "完整、连续的指标短语" in METRIC_TIME_EXTRACTION_RULES
    assert "指标短语前后的业务限定词" in METRIC_TIME_EXTRACTION_RULES
    assert "宁可保留更长的原文短语，不得缩短" in METRIC_TIME_EXTRACTION_RULES
    assert "渠道订单平均金额" in METRIC_TIME_EXTRACTION_RULES
    assert "必须保留用户原文中完整、连续的指标短语及其业务限定词" in INTENT_SYSTEM_PROMPT


def test_agent_prompts_include_typical_few_shot_examples():
    assert "只替换上一轮时间的追问" in REWRITE_SYSTEM_PROMPT
    assert '"original_question"' in REWRITE_SYSTEM_PROMPT
    assert '"rewrite_question"' in REWRITE_SYSTEM_PROMPT
    assert "message_type" not in REWRITE_SYSTEM_PROMPT
    assert "missing_slots" not in REWRITE_SYSTEM_PROMPT
    assert "示例 1：排名查询" in INTENT_SYSTEM_PROMPT
    assert '"order_direction":"desc","limit":5' in INTENT_SYSTEM_PROMPT
    assert "示例 2：明确筛选值" in DIMENSION_SYSTEM_PROMPT
    assert '"role":"filter","value":"100011"' in DIMENSION_SYSTEM_PROMPT
    assert "示例 3：用途不明确" in DIMENSION_SYSTEM_PROMPT
