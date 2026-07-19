from apps.chatbi.models import (
    ChartGenerationMessage,
    GenerationHistoryLog,
    GenerationHistoryProjectionData,
    SQLGenerationMessage,
)
from apps.chatbi.services import GenerationHistoryProjectionService


def test_projection_uses_latest_log_and_last_requested_round():
    service = GenerationHistoryProjectionService()

    result = service.project(
        GenerationHistoryProjectionData(
            sql_logs=[
                GenerationHistoryLog(
                    record_id=10,
                    messages=[
                        {"type": "human", "content": "older question"},
                        {"type": "ai", "content": "older answer"},
                    ],
                ),
                GenerationHistoryLog(
                    record_id=20,
                    messages=[
                        {
                            "type": "human",
                            "content": "system context",
                            "sqlbot_system": True,
                        },
                        {"type": "human", "content": "first question"},
                        {"type": "ai", "content": "first answer"},
                        {"type": "human", "content": "last question"},
                        {"type": "tool", "content": "ignored"},
                        {"type": "ai", "content": "last answer"},
                    ],
                ),
            ],
            chart_logs=[
                GenerationHistoryLog(
                    record_id=20,
                    messages=[
                        {"type": "human", "content": "chart question"},
                        {"type": "ai", "content": "chart answer"},
                    ],
                )
            ],
            round_limit=1,
        )
    )

    assert result.sql_history == [
        SQLGenerationMessage(role="human", content="last question"),
        SQLGenerationMessage(role="ai", content="last answer"),
    ]
    assert result.chart_history == [
        ChartGenerationMessage(role="human", content="chart question"),
        ChartGenerationMessage(role="ai", content="chart answer"),
    ]


def test_projection_uses_regenerate_record_instead_of_latest_log():
    service = GenerationHistoryProjectionService()
    logs = [
        GenerationHistoryLog(
            record_id=10,
            messages=[
                {"type": "human", "content": "selected question"},
                {"type": "ai", "content": "selected answer"},
            ],
        ),
        GenerationHistoryLog(
            record_id=20,
            messages=[
                {"type": "human", "content": "latest question"},
                {"type": "ai", "content": "latest answer"},
            ],
        ),
    ]

    result = service.project(
        GenerationHistoryProjectionData(
            sql_logs=logs,
            regenerate_record_id=10,
            round_limit=2,
        )
    )

    assert [message.content for message in result.sql_history] == [
        "selected question",
        "selected answer",
    ]


def test_projection_does_not_fall_back_when_regenerate_record_is_missing():
    result = GenerationHistoryProjectionService().project(
        GenerationHistoryProjectionData(
            sql_logs=[
                GenerationHistoryLog(
                    record_id=20,
                    messages=[{"type": "human", "content": "latest question"}],
                )
            ],
            regenerate_record_id=10,
            round_limit=1,
        )
    )

    assert result.sql_history == []


def test_projection_returns_empty_history_without_positive_round_limit_or_human_message():
    service = GenerationHistoryProjectionService()
    log = GenerationHistoryLog(
        record_id=10,
        messages=[{"type": "ai", "content": "answer without question"}],
    )

    assert service.project(
        GenerationHistoryProjectionData(sql_logs=[log], round_limit=0)
    ).sql_history == []
    assert service.project(
        GenerationHistoryProjectionData(sql_logs=[log], round_limit=1)
    ).sql_history == []
