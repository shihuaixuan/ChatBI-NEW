import pytest

from apps.chatbi.errors import FinalReplyProjectionError
from apps.chatbi.models import (
    FinalReplyProjectionData,
    QueryFinalReplyProjectionData,
)
from apps.chatbi.services.generation import (
    project_final_reply,
    project_query_final_reply,
)


def test_final_reply_projection_combines_answer_recommendations_and_chart():
    result = project_final_reply(
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
    result = project_final_reply(FinalReplyProjectionData())

    assert result.final_answer == "暂时无法生成完整回答，请稍后重试。"
    assert result.recommendations == []
    assert result.chart == {}
    assert result.metadata == {"source": "real_chatbi_v1"}


def test_final_reply_projection_preserves_existing_answer_string_conversion():
    result = project_final_reply(
        FinalReplyProjectionData(
            answer={"answer": 1234},
            recommendations={"questions": ("查看昨日访问量",)},
        )
    )

    assert result.final_answer == "1234"
    assert result.recommendations == ["查看昨日访问量"]


def test_query_final_reply_requires_successful_execution():
    with pytest.raises(FinalReplyProjectionError) as exc_info:
        project_query_final_reply(
            QueryFinalReplyProjectionData(
                answer_markdown="答案",
                execution=None,
            )
        )

    assert exc_info.value.error_code == "execution_required_before_finish"


def test_query_final_reply_appends_manual_sql_note():
    result = project_query_final_reply(
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
    result = project_query_final_reply(
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


def test_query_final_reply_uses_full_rows_instead_of_model_numbers():
    result = project_query_final_reply(
        QueryFinalReplyProjectionData(
            answer_markdown="6月11日GMV为错误的30,408.72。",
            execution={
                "sql": "select stat_date, gmv_total from orders",
                "fields": ["stat_date", "gmv_total"],
                "sql_source": "compiled",
            },
            rows=[
                {"stat_date": "2026-06-11", "gmv_total": 7132.4},
                {"stat_date": "2026-06-12", "gmv_total": 21951.64},
            ],
        )
    )

    assert "30,408.72" not in result.answer
    assert "2026-06-11 | 7132.4" in result.answer
    assert "2026-06-12 | 21951.64" in result.answer


def test_query_final_reply_calculates_share_from_grouped_rows():
    result = project_query_final_reply(
        QueryFinalReplyProjectionData(
            answer_markdown="模型生成的占比",
            execution={
                "sql": "select channel_type, sum(gmv) from orders group by channel_type",
                "fields": ["channel_type", "customer_gmv"],
                "sql_source": "compiled",
            },
            rows=[
                {"channel_type": "线上", "customer_gmv": 75},
                {"channel_type": "线下", "customer_gmv": 25},
            ],
            intent={"intent_type": "share_analysis"},
        )
    )

    assert "| channel_type | customer_gmv | 占比 |" in result.answer
    assert "| 线上 | 75 | 75.00% |" in result.answer
    assert "| 线下 | 25 | 25.00% |" in result.answer
