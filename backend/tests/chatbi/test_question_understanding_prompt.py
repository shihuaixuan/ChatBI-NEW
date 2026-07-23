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
