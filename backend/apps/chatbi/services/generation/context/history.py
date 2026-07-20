"""旧生成日志到稳定历史消息的投影规则（纯函数）。"""

from collections.abc import Callable
from typing import Any, Literal, TypeVar, cast

from apps.chatbi.models.dto.chart_generation import ChartGenerationMessage
from apps.chatbi.models.dto.generation_history import (
    GenerationHistoryLog,
    GenerationHistoryProjectionData,
    GenerationHistoryProjectionResult,
)
from apps.chatbi.models.dto.sql_generation import SQLGenerationMessage

GenerationMessage = TypeVar(
    "GenerationMessage",
    SQLGenerationMessage,
    ChartGenerationMessage,
)


def project_generation_history(
    data: GenerationHistoryProjectionData,
) -> GenerationHistoryProjectionResult:
    """把旧生成日志投影为稳定的 SQL 与图表历史消息。"""

    sql_messages = _select_messages(data.sql_logs, data.regenerate_record_id)
    chart_messages = _select_messages(data.chart_logs, data.regenerate_record_id)
    return GenerationHistoryProjectionResult(
        sql_history=_project_messages(
            sql_messages,
            data.round_limit,
            SQLGenerationMessage,
        ),
        chart_history=_project_messages(
            chart_messages,
            data.round_limit,
            ChartGenerationMessage,
        ),
    )


def _select_messages(
    logs: list[GenerationHistoryLog],
    regenerate_record_id: int | None,
) -> list[dict[str, Any]]:
    selected_log = logs[-1] if logs else None
    if regenerate_record_id:
        selected_log = next(
            (log for log in logs if log.record_id == regenerate_record_id),
            None,
        )
    if selected_log is None or selected_log.messages is None:
        return []
    return [
        message
        for message in selected_log.messages
        if message.get("sqlbot_system") not in (True,)
    ]


def _project_messages(
    messages: list[dict[str, Any]],
    round_limit: int,
    message_factory: Callable[..., GenerationMessage],
) -> list[GenerationMessage]:
    projected: list[GenerationMessage] = []
    for message in _last_conversation_rounds(messages, round_limit):
        role = message.get("type")
        if role not in ("human", "ai"):
            continue
        projected.append(
            message_factory(
                role=cast(Literal["human", "ai"], role),
                content=cast(str, message.get("content")),
            )
        )
    return projected


def _last_conversation_rounds(
    messages: list[dict[str, Any]],
    rounds: int,
) -> list[dict[str, Any]]:
    if not messages or rounds <= 0:
        return []

    human_indices = [
        index
        for index, message in enumerate(messages)
        if message.get("type") == "human"
    ]
    if not human_indices:
        return []

    start_index = (
        human_indices[0]
        if len(human_indices) <= rounds
        else human_indices[-rounds]
    )
    return messages[start_index:]


__all__ = ["project_generation_history"]
