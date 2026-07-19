from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.models.orm import ChatRecord


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
    records: list[ChatRecord | dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ChatInfo",
    "ConversationBinding",
    "ConversationCreateData",
    "CreateChat",
    "RenameChat",
]
