from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentQuestionRequest(BaseModel):
    chat_id: int = Field(gt=0)
    question: str = Field(min_length=1)
    datasource_id: int | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("question cannot be empty")
        return value


class AgentClarificationRequest(BaseModel):
    # 结构化选项回答或自由文本，二者至少其一。
    selections: list[dict[str, Any]] = Field(default_factory=list)
    text: str | None = None


class AgentConfig(BaseModel):
    enabled: bool = False
    datasource_allowlist: list[int] = Field(default_factory=list)
    max_steps: int = 12
    max_sql_retries: int = 2
    max_clarifications: int = 2
    repeat_fuse_threshold: int = 3
    timeout_seconds: int = 120
    token_budget: int = 100_000
    default_limit: int = 100
    sample_rows: int = 10
    summary_max_chars: int = 4000


class AgentEventPayload(BaseModel):
    type: str
    content: Any = None
    record_id: int | None = None
    run_id: int | None = None
    sequence: int | None = None
