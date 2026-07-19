from typing import Any

from pydantic import BaseModel, Field


class AiModelQuestion(BaseModel):
    """旧 Chat 模型编排使用的查询上下文。"""

    question: str | None = None
    ai_modal_id: int | None = None
    ai_modal_name: str | None = None
    engine: str = ""
    db_schema: str = ""
    sql: str = ""
    rule: str = ""
    fields: str = ""
    data: str = ""
    lang: str = "简体中文"
    filter: str | list[Any] = Field(default_factory=list)
    sub_query: list[dict[str, Any]] | None = None
    terminologies: str = ""
    data_training: str = ""
    semantic_context: str = ""
    custom_prompt: str = ""
    error_msg: str = ""
    regenerate_record_id: int | None = None
    sample_data: str = ""
    sqlbot_name: str = "Numora"


class ChatQuestion(AiModelQuestion):
    """绑定会话和可选数据源的旧 Chat 查询上下文。"""

    chat_id: int
    datasource_id: int | None = None


__all__ = ["AiModelQuestion", "ChatQuestion"]
