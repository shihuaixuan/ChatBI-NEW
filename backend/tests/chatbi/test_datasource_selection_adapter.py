from apps.chatbi.models import (
    DatasourceSelectionCandidate,
    DatasourceSelectionData,
)
from infrastructure.datasource_selection import (
    TemplateDatasourceSelectionPromptBuilder,
)


def test_datasource_prompt_contains_only_stable_candidate_fields():
    data = DatasourceSelectionData(
        record_id=10,
        question="查询本月销售额",
        candidates=[
            DatasourceSelectionCandidate(
                id=101,
                name="销售库",
                description="订单与销售数据",
            )
        ],
        language="简体中文",
        assistant_name="Numora",
        auto_select=False,
    )

    messages = TemplateDatasourceSelectionPromptBuilder().build(data)

    assert [message.role for message in messages] == ["system", "human"]
    assert "查询本月销售额" in messages[1].content
    assert '"id":101' in messages[1].content
    assert '"name":"销售库"' in messages[1].content
    assert '"description":"订单与销售数据"' in messages[1].content
