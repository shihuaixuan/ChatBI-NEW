from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgenticQuestionRequest(BaseModel):
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


class AgenticClarificationAnswerItem(BaseModel):
    slot: str = Field(min_length=1)
    value: str = Field(min_length=1)


class AgenticClarificationOption(BaseModel):
    slot: str | None = None
    label: str | None = None
    value: str | None = None
    multiple: bool = False
    asset_type: str | None = None
    asset_id: int | None = None


class AgenticClarificationRequest(BaseModel):
    answers: list[AgenticClarificationAnswerItem] = Field(default_factory=list)


class AgenticSlotCandidateInfo(BaseModel):
    display_name: str | None = None
    raw_text: str | None = None
    asset_type: str | None = None
    asset_id: int | None = None
    score: float | None = None
    source: str | None = None


class AgenticSlotIssueInfo(BaseModel):
    slot: str
    raw_text: str | None = None
    reason: str | None = None
    candidates: list[AgenticSlotCandidateInfo] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class AgenticUnderstandingSummary(BaseModel):
    normalized_question: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    low_confidence_slots: list[AgenticSlotIssueInfo] = Field(default_factory=list)
    ambiguous_slots: list[AgenticSlotIssueInfo] = Field(default_factory=list)
    conflict_slots: list[AgenticSlotIssueInfo] = Field(default_factory=list)


class AgenticStepInfo(BaseModel):
    index: int
    action: str
    status: str
    tool_name: str | None = None
    strategy: str | None = None
    duration_ms: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    understanding: AgenticUnderstandingSummary | None = None


class AgenticTraceResponse(BaseModel):
    record_id: int
    run_id: int | None = None
    status: str | None = None
    steps: list[AgenticStepInfo] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    clarification: dict[str, Any] | None = None


class AgenticEventPayload(BaseModel):
    type: str
    content: Any = None
    record_id: int | None = None
    run_id: int | None = None
    step_index: int | None = None


class ToolResult(BaseModel):
    success: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    error_code: str | None = None


class AgenticDecision(BaseModel):
    action: str
    tool_name: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgenticContext(BaseModel):
    user_id: int | None = None
    oid: int = 1
    assistant_id: int | None = None


class AgenticConfig(BaseModel):
    enabled: bool = False
    assistant_enabled: bool = False
    datasource_allowlist: list[int] = Field(default_factory=list)
    max_steps: int = 12
    default_limit: int = 100
