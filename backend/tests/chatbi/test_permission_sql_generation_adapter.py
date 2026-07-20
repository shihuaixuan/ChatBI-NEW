from apps.chatbi.adapters import permission_sql_generation
from apps.chatbi.adapters.permission_sql_generation import (
    TemplatePermissionSQLGenerationPromptBuilder,
)
from apps.chatbi.models import (
    PermissionSQLFilter,
    PermissionSQLGenerationData,
)


def test_permission_sql_prompt_keeps_system_and_user_message_contract(monkeypatch):
    monkeypatch.setattr(
        permission_sql_generation,
        "get_permissions_template",
        lambda: {
            "system": "系统:{lang}:{engine}:{sqlbot_name}",
            "user": "当前:{sql}:{filter}",
        },
    )

    messages = TemplatePermissionSQLGenerationPromptBuilder().build(
        PermissionSQLGenerationData(
            record_id=10,
            sql="SELECT * FROM 订单表",
            filters=[
                PermissionSQLFilter(
                    table="订单表",
                    condition="区域 = '华东'",
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
        "当前:SELECT * FROM 订单表:"
        "[{\"table\": \"订单表\", \"filter\": \"区域 = '华东'\"}]"
    )
