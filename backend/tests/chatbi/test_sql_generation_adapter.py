from apps.chatbi.adapters import sql_generation
from apps.chatbi.adapters.sql_generation import TemplateSQLGenerationPromptBuilder
from apps.chatbi.models import SQLGenerationData, SQLGenerationMessage


def _base_template() -> dict[str, str]:
    return {
        "process_check": "基础检查",
        "query_limit": "限制规则",
        "no_query_limit": "无限制规则",
        "multi_table_condition": "多表条件",
        "system": "系统:{lang}:{process_check}:{sqlbot_name}",
        "generate_rules": (
            "规则:{lang}:{sqlbot_name}:{base_sql_rules}:{basic_sql_examples}:"
            "{example_engine}:{example_answer_1}:{example_answer_2}:{example_answer_3}"
        ),
        "generate_basic_info": "结构:{engine}:{schema}:{sample_data}",
        "generate_custom_prompt_info": "自定义:{custom_prompt}",
        "generate_terminologies_info": "术语:{terminologies}",
        "generate_data_training_info": "示例:{data_training}",
        "regenerate_hint": "重新生成：",
        "user": (
            "当前:{lang}:{engine}:{schema}:{question}:{rule}:{current_time}:"
            "{error_msg}:{change_title}"
        ),
    }


def _sql_template() -> dict[str, str]:
    return {
        "process_check": "MySQL检查",
        "other_rule": "其他:{multi_table_condition}",
        "quot_rule": "引号规则;",
        "limit_rule": "limit规则;",
        "basic_example": "基础示例",
        "example_engine": "MySQL",
        "example_answer_1_with_limit": "示例1-limit",
        "example_answer_2_with_limit": "示例2-limit",
        "example_answer_3_with_limit": "示例3-limit",
        "example_answer_1": "示例1",
        "example_answer_2": "示例2",
        "example_answer_3": "示例3",
    }


def _data(*, enable_query_limit: bool = True) -> SQLGenerationData:
    return SQLGenerationData(
        record_id=10,
        question="销售趋势",
        database_type="mysql",
        engine="MySQL 8.0",
        schema="orders(month, sales)",
        sample_data='[{"month":"1月","sales":100}]',
        language="简体中文",
        assistant_name="Numora",
        current_time="2026-07-19 16:00:00",
        rule="只统计已支付订单",
        error_message="上一轮字段错误",
        custom_prompt="金额保留两位小数",
        terminologies="销售额：已支付订单金额",
        data_training="历史 SQL 示例",
        enable_query_limit=enable_query_limit,
        change_title=True,
        regenerate=True,
        history=[
            SQLGenerationMessage(role="human", content="历史问题"),
            SQLGenerationMessage(role="ai", content="历史回答"),
        ],
    )


def test_sql_prompt_keeps_context_history_and_current_message_order(monkeypatch):
    monkeypatch.setattr(sql_generation, "get_sql_template", _base_template)
    monkeypatch.setattr(
        sql_generation,
        "get_sql_example_template",
        lambda database_type: _sql_template(),
    )

    messages = TemplateSQLGenerationPromptBuilder().build(_data())

    assert [message.role for message in messages] == [
        "system",
        "human",
        "ai",
        "human",
        "ai",
        "human",
        "ai",
        "human",
        "ai",
        "human",
        "ai",
        "human",
        "ai",
        "human",
    ]
    assert [message.system_context for message in messages] == [
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert messages[5].content == "自定义:金额保留两位小数"
    assert messages[7].content == "术语:销售额：已支付订单金额"
    assert messages[9].content == "示例:历史 SQL 示例"
    assert messages[11:13] == _data().history
    assert "限制规则" in messages[1].content
    assert "示例1-limit" in messages[1].content
    assert "重新生成：销售趋势" in messages[-1].content
    assert messages[-1].content.endswith("上一轮字段错误:True")


def test_sql_prompt_uses_no_limit_rules_and_examples(monkeypatch):
    monkeypatch.setattr(sql_generation, "get_sql_template", _base_template)
    monkeypatch.setattr(
        sql_generation,
        "get_sql_example_template",
        lambda database_type: _sql_template(),
    )

    messages = TemplateSQLGenerationPromptBuilder().build(
        _data(enable_query_limit=False)
    )

    assert "无限制规则" in messages[1].content
    assert "示例1:" in messages[1].content
    assert "示例1-limit" not in messages[1].content
