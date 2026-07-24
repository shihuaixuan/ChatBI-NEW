from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from apps.conversation.models.dto.chat_history import ChatRecordResult


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    dataset_id: int
    dataset_name: str
    datasource_id: int
    datasource_name: str
    datasource_type: str
    datasource_type_name: str


@dataclass(frozen=True, slots=True)
class ConversationCreateData:
    user_id: int
    workspace_id: int
    question: str
    origin: int
    created_at: datetime
    binding: ConversationBinding | None
    create_welcome_record: bool
    recommended_questions: list[str]


class CreateChat(BaseModel):
    id: int | None = None
    question: str | None = None
    dataset_id: int | None = None
    datasource: int | None = None
    origin: int | None = 0


class RenameChat(BaseModel):
    id: int | None = None
    brief: str = ""
    brief_generate: bool = True


class ConversationSummary(BaseModel):
    """保持 `/chat/list` 既有字段的会话摘要。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None
    oid: int | None
    create_time: datetime
    create_by: int
    brief: str = Field(max_length=64)
    chat_type: str = Field(default="chat", max_length=20)
    dataset_id: int | None = None
    datasource: SkipValidation[int]
    engine_type: str = Field(max_length=64)
    origin: int | None
    brief_generate: bool = False
    recommended_question_answer: SkipValidation[str]
    recommended_question: SkipValidation[str]
    recommended_generate: bool = False


class ChatInfo(BaseModel):
    id: int | None = None
    create_time: datetime | None = None
    create_by: int | None = None
    brief: str = ""
    chat_type: str = "chat"
    dataset_id: int | None = None
    dataset_name: str = ""
    dataset_exists: bool = True
    datasource: int | None = None
    engine_type: str = ""
    ds_type: str = ""
    datasource_name: str = ""
    datasource_exists: bool = True
    recommended_question: str | None = None
    recommended_generate: bool | None = False
    records: list[ChatRecordResult | dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ChatInfo",
    "ConversationBinding",
    "ConversationCreateData",
    "ConversationSummary",
    "CreateChat",
    "RenameChat",
]
