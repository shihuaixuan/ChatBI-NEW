from apps.chatbi.adapters import dynamic_sql_generation
from apps.chatbi.adapters.dynamic_sql_generation import (
    TemplateDynamicSQLGenerationPromptBuilder,
)
from apps.chatbi.models import (
    DynamicSQLGenerationData,
    DynamicSQLSubqueryMapping,
)


def test_dynamic_sql_prompt_keeps_system_and_user_message_contract(monkeypatch):
    monkeypatch.setattr(
        dynamic_sql_generation,
        "get_dynamic_template",
        lambda: {
            "system": "系统:{lang}:{engine}:{sqlbot_name}",
            "user": "当前:{sql}:{sub_query}",
        },
    )

    messages = TemplateDynamicSQLGenerationPromptBuilder().build(
        DynamicSQLGenerationData(
            sql="SELECT * FROM 订单表",
            subqueries=[
                DynamicSQLSubqueryMapping(
                    table="订单表",
                    query="sqlbot_dynamic_subsql_订单表",
                )
            ],
            language="简体中文",
            engine="MySQL 8.0",
            assistant_name="Numora",
        )
    )

    assert [message.role for message in messages] == ["system", "human"]
    assert [message.system_context for message in messages] == [True, False]
    assert messages[0].content == "系统:简体中文:MySQL 8.0:Numora"
    assert messages[1].content == (
        '当前:SELECT * FROM 订单表:[{"table": "订单表", '
        '"query": "sqlbot_dynamic_subsql_订单表"}]'
    )
