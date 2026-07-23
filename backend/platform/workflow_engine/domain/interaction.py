from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InteractionStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InteractionRequest(BaseModel):
    """Runtime 暂停时创建的结构化人工输入请求。"""

    interaction_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    status: InteractionStatus = InteractionStatus.PENDING
    response_schema: dict[str, Any]
    allowed_update_paths: list[str] = Field(default_factory=list)
    prompt: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    response: dict[str, Any] | None = None
    created_at: datetime
    expires_at: datetime | None = None
    answered_at: datetime | None = None
