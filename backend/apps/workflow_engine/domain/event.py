from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, PositiveInt


class WorkflowEvent(BaseModel):
    """按 Run 顺序追加的审计和前端事件。"""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: PositiveInt
    event_type: str = Field(min_length=1)
    node_name: str | None = None
    node_execution_id: str | None = None
    public_payload: dict[str, Any] = Field(default_factory=dict)
    internal_payload_ref: str | None = None
    created_at: datetime
