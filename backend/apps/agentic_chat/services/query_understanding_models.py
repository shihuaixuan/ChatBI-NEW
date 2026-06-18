from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

QueryIntent = Literal[
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "comparison_analysis",
    "detail_query",
    "share_analysis",
    "anomaly_analysis",
    "chitchat",
    "unknown",
]


class QueryUnderstandingConfig(BaseModel):
    model_enabled: bool = True
    timeout_ms: int = 1500
    min_confidence: float = 0.65
    core_slot_min_confidence: float = 0.65
    metric_accept_score: float = 0.78
    metric_ambiguity_gap: float = 0.12
    exact_alias_accept: bool = True
    clarification_max_options: int = 6


class SlotCandidate(BaseModel):
    display_name: str
    raw_text: str | None = None
    asset_type: str | None = None
    asset_id: int | None = None
    score: float = 0.0
    source: str = "semantic"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("score must be between 0.0 and 1.0")
        return value


class UnderstandingSlot(BaseModel):
    name: str
    display_name: str | None = None
    raw_text: str | None = None
    value: Any = None
    confidence: float = 0.0
    source: str = "unknown"
    asset_type: str | None = None
    asset_id: int | None = None
    confirmed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value


class SlotIssue(BaseModel):
    slot: str
    raw_text: str | None = None
    reason: str
    candidates: list[SlotCandidate] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class QueryUnderstandingContext(BaseModel):
    question: str
    chat_id: int
    record_id: int
    oid: int = 1
    user_id: int | None = None
    datasource_id: int | None = None
    current_time: datetime = Field(default_factory=datetime.now)
    confirmed_slots: dict[str, Any] = Field(default_factory=dict)
    history_slots: dict[str, Any] = Field(default_factory=dict)
    semantic_candidates: list[SlotCandidate | dict[str, Any]] = Field(default_factory=list)
    terminology_candidates: list[dict[str, Any]] = Field(default_factory=list)
    schema_summary: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("semantic_candidates", mode="after")
    @classmethod
    def normalize_semantic_candidates(cls, value: list[SlotCandidate | dict[str, Any]]) -> list[SlotCandidate]:
        return [item if isinstance(item, SlotCandidate) else SlotCandidate(**item) for item in value]


class QueryUnderstandingResult(BaseModel):
    normalized_question: str
    intent: QueryIntent = "unknown"
    intent_confidence: float = 0.0
    slots: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    low_confidence_slots: list[SlotIssue] = Field(default_factory=list)
    ambiguous_slots: list[SlotIssue] = Field(default_factory=list)
    conflict_slots: list[SlotIssue] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    can_answer_with_assumption: bool = False

    @field_validator("confidence", "intent_confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    def dump_for_tool(self) -> dict[str, Any]:
        return self.model_dump()

