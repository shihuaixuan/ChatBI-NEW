from apps.chatbi.models import ChartGenerationData, ChartGenerationMessage
from infrastructure import chart_generation
from infrastructure.chart_generation import TemplateChartGenerationPromptBuilder


def test_chart_prompt_keeps_system_history_and_current_message_order(monkeypatch):
    monkeypatch.setattr(
        chart_generation,
        "get_chart_template",
        lambda: {
            "system": "系统:{lang}:{sqlbot_name}",
            "generate_rules": "规则:{lang}",
            "user": "当前:{lang}:{sql}:{question}:{rule}:{chart_type}:{schema}",
        },
    )
    history = [
        ChartGenerationMessage(role="human", content="历史问题"),
        ChartGenerationMessage(role="ai", content="历史图表"),
    ]

    messages = TemplateChartGenerationPromptBuilder().build(
        ChartGenerationData(
            record_id=10,
            question="销售趋势",
            sql="SELECT month, sales FROM orders",
            schema="orders(month, sales)",
            chart_type="line",
            language="简体中文",
            assistant_name="Numora",
            rule="保留两位小数",
            history=history,
        )
    )

    assert [message.role for message in messages] == [
        "system",
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
        False,
        False,
        False,
    ]
    assert messages[3:5] == history
    assert messages[0].content == "系统:简体中文:Numora"
    assert messages[1].content == "规则:简体中文"
    assert messages[-1].content == (
        "当前:简体中文:SELECT month, sales FROM orders:销售趋势:"
        "保留两位小数:line:orders(month, sales)"
    )
