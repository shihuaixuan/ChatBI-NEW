import pytest

from apps.chatbi.models import (
    FinalReplyProjectionData,
    QueryFinalReplyProjectionData,
)
from apps.chatbi.services import (
    FinalReplyProjectionError,
    FinalReplyProjectionService,
)


def test_final_reply_projection_combines_answer_recommendations_and_chart():
    result = FinalReplyProjectionService().project(
        FinalReplyProjectionData(
            answer={"answer": "今日访问量为 1,234。", "warnings": []},
            recommendations={"questions": ["查看昨日访问量", "按店铺分析"]},
            chart={"profile": "table", "chart_candidates": ["table"]},
        )
    )

    assert result.model_dump(mode="json") == {
        "final_answer": "今日访问量为 1,234。",
        "recommendations": ["查看昨日访问量", "按店铺分析"],
        "chart": {"profile": "table", "chart_candidates": ["table"]},
        "metadata": {"source": "real_chatbi_v1"},
    }


def test_final_reply_projection_uses_stable_default_answer():
    result = FinalReplyProjectionService().project(FinalReplyProjectionData())

    assert result.final_answer == "暂时无法生成完整回答，请稍后重试。"
    assert result.recommendations == []
    assert result.chart == {}
    assert result.metadata == {"source": "real_chatbi_v1"}


def test_final_reply_projection_preserves_existing_answer_string_conversion():
    result = FinalReplyProjectionService().project(
        FinalReplyProjectionData(
            answer={"answer": 1234},
            recommendations={"questions": ("查看昨日访问量",)},
        )
    )

    assert result.final_answer == "1234"
    assert result.recommendations == ["查看昨日访问量"]


def test_query_final_reply_requires_successful_execution():
    with pytest.raises(FinalReplyProjectionError) as exc_info:
        FinalReplyProjectionService.project_query_answer(
            QueryFinalReplyProjectionData(
                answer_markdown="答案",
                execution=None,
            )
        )

    assert exc_info.value.error_code == "execution_required_before_finish"


def test_query_final_reply_appends_manual_sql_note():
    result = FinalReplyProjectionService.project_query_answer(
        QueryFinalReplyProjectionData(
            answer_markdown="答案",
            execution={
                "sql": "select 1",
                "fields": ["value"],
                "sql_source": "manual",
            },
        )
    )

    assert "非标准指标口径" in result.answer
    assert result.sql == "select 1"
    assert result.non_standard is True


def test_query_final_reply_builds_chart_from_execution_fields():
    result = FinalReplyProjectionService.project_query_answer(
        QueryFinalReplyProjectionData(
            answer_markdown="答案",
            execution={
                "sql": "select city, gmv from orders",
                "fields": ["city", "gmv"],
                "sql_source": "compiled",
            },
            chart_type="bar",
        )
    )

    assert result.answer == "答案"
    assert result.chart == {"type": "bar", "x": "city", "y": ["gmv"]}
    assert result.non_standard is False
