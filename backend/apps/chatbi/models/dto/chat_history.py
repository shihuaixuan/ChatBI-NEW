from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRecordResult(BaseModel):
    """会话历史中的单条问数记录。"""

    id: int | None = None
    chat_id: int | None = None
    ai_modal_id: int | None = None
    first_chat: bool = False
    create_time: datetime | None = None
    finish_time: datetime | None = None
    question: str | None = None
    sql_answer: str | None = None
    sql: str | None = None
    dataset_id: int | None = None
    datasource: int | None = None
    data: str | None = None
    chart_answer: str | None = None
    chart: str | None = None
    analysis: str | None = None
    predict: str | None = None
    predict_data: str | None = None
    recommended_question: str | None = None
    datasource_select_answer: str | None = None
    finish: bool | None = None
    status: str | None = None
    trace_id: str | None = None
    # 响应层保持可选，兼容尚未投影执行类型的历史查询结果。
    execution_type: str | None = None
    error: str | None = None
    analysis_record_id: int | None = None
    predict_record_id: int | None = None
    regenerate_record_id: int | None = None
    sql_reasoning_content: str | None = None
    chart_reasoning_content: str | None = None
    analysis_reasoning_content: str | None = None
    predict_reasoning_content: str | None = None
    duration: float | None = None
    total_tokens: int | None = None


class ChatLogHistoryItem(BaseModel):
    """会话记录中的单个执行步骤。"""

    start_time: datetime | None = None
    finish_time: datetime | None = None
    duration: float | None = None
    total_tokens: int | None = None
    operate: str | None = None
    local_operation: bool | None = False
    message: str | dict[str, Any] | list[Any] | None = None
    error: bool | None = False


class ChatLogHistory(BaseModel):
    """会话记录的执行历史汇总。"""

    start_time: datetime | None = None
    finish_time: datetime | None = None
    duration: float | None = None
    total_tokens: int | None = None
    steps: list[ChatLogHistoryItem | dict[str, Any]] = Field(default_factory=list)


__all__ = ["ChatLogHistory", "ChatLogHistoryItem", "ChatRecordResult"]
