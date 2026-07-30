from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

IntentType = Literal[
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "comparison_analysis",
    "detail_query",
    "share_analysis",
    "anomaly_analysis",
    "unknown",
]
RequiredSlotType = Literal[
    "metric",
    "dimension",
    "time_dimension",
    "time_range",
    "filter",
    "order",
    "limit",
    "comparison_target",
]
IntentTypeT = TypeVar("IntentTypeT")
DimensionSlotT = TypeVar("DimensionSlotT")
TimeRangeT = TypeVar("TimeRangeT")
RequiredSlotTypeT = TypeVar("RequiredSlotTypeT")


class QuestionClassificationOutputBase(BaseModel):
    """Graph 与后续 ChatBI 入口共享的问题分类输出契约。"""

    category: Literal["forbidden", "chitchat", "data", "followup"]
    reason: str
    risk_level: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(default=0.0, ge=0, le=1)


class QuestionRewriteOutputBase(BaseModel):
    """Agent 与 Graph 共享的问题重写最小契约。"""

    rewritten_question: str
    need_user_input: bool = False
    missing_slots: list[str] = Field(default_factory=list)


class QuestionRewriteProjectionOutput(QuestionRewriteOutputBase):
    """Graph 问题重写节点使用的稳定投影契约。"""

    image_profile_hint: str | None = None


class NaturalLanguageIntentOutputBase(
    BaseModel,
    Generic[IntentTypeT, DimensionSlotT, TimeRangeT, RequiredSlotTypeT],
):
    """自然语言意图公共字段，不包含执行器专属信息。"""

    intent_type: IntentTypeT
    confidence: float = Field(ge=0, le=1)
    metric_mentions: list[str] = Field(default_factory=list)
    dimension_mentions: list[str] = Field(default_factory=list)
    # Graph 将此类型参数化为字典，避免 Agent DTO 的默认字段扩展节点输出。
    dimension_slots: list[DimensionSlotT] = Field(default_factory=list)
    time_mentions: list[str] = Field(default_factory=list)
    time_range: TimeRangeT
    filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    required_slot_types: list[RequiredSlotTypeT] = Field(default_factory=list)
    query_shape: dict[str, Any] = Field(default_factory=dict)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)


class QuestionRewriteOutput(QuestionRewriteOutputBase):
    """ChatBI 权威问题重写输出，Agent 使用严格扩展字段。"""

    model_config = ConfigDict(extra="forbid")

    rewritten_question: str = Field(min_length=1)
    message_type: Literal["new_question", "followup", "clarification_reply"]
    inherited_context: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)


class DimensionSlot(BaseModel):
    """自然语言维度槽位，不绑定字段或语义资产。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: Literal["group_by", "filter", "ambiguous"]
    value: str | int | float | bool | None = None
    value_status: Literal["provided", "not_provided", "ambiguous"] = "not_provided"
    value_confidence: float = Field(default=0.0, ge=0, le=1)


class TimeRange(BaseModel):
    """保留原始时间表达，并承载系统确定性生成的时间 AST。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = None
    value_status: Literal["provided", "not_provided"] = "not_provided"
    normalized: dict[str, Any] | None = None


class IntentRecognitionOutput(
    NaturalLanguageIntentOutputBase[
        IntentType,
        DimensionSlot,
        TimeRange,
        RequiredSlotType,
    ]
):
    """ChatBI 权威自然语言意图，不包含工具或资产绑定。"""

    model_config = ConfigDict(extra="forbid")

    time_range: TimeRange = Field(default_factory=TimeRange)


class DimensionRecognitionOutput(BaseModel):
    """独立维度子任务输出，避免综合意图识别遗漏业务对象。"""

    model_config = ConfigDict(extra="forbid")

    dimension_mentions: list[str] = Field(default_factory=list)
    dimension_slots: list[DimensionSlot] = Field(default_factory=list)
    residual_filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dimension_coverage(self) -> DimensionRecognitionOutput:
        """维度 mention 与结构化槽位必须一一对应。"""

        mentions = _unique_strings(self.dimension_mentions)
        slot_names = [slot.name for slot in self.dimension_slots]
        if len(slot_names) != len(set(slot_names)) or set(mentions) != set(slot_names):
            raise ValueError("dimension_mentions 与 dimension_slots 必须一一对应")
        self.dimension_mentions = mentions
        return self


class IntentValidationOutput(BaseModel):
    """确定性校验结果，供 Agent 决定是否必须先澄清。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "clarification_required"]
    reason_codes: list[str] = Field(default_factory=list)
    clarification_slots: list[str] = Field(default_factory=list)


class QuestionUnderstandingOutput(BaseModel):
    """问题理解统一输出，是后续 Agent 与工具共享的唯一事实源。"""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    message_type: Literal["new_question", "followup", "clarification_reply"]
    rewritten_question: str
    inherited_context: dict[str, Any] = Field(default_factory=dict)
    intent: IntentRecognitionOutput
    validation: IntentValidationOutput


@dataclass(frozen=True)
class QuestionUnderstandingOutcome:
    """统一业务输出与问题理解模型调用的累计用量。"""

    output: QuestionUnderstandingOutput
    usage_metadata: dict[str, int]


@dataclass(frozen=True, slots=True)
class QuestionIntentProjectionData:
    """Graph 三类自然语言意图子任务的确定性合并输入。"""

    shape: dict[str, Any]
    semantic: dict[str, Any]
    dimensions: dict[str, Any]
    user_feedback: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionIntentProjectionResult:
    """不绑定 Graph Schema 的稳定自然语言意图投影。"""

    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationData:
    """问题重写和自然语言意图的确定性校验输入。"""

    rewrite_need_user_input: bool = False
    rewrite_missing_slots: tuple[str, ...] = ()
    intent_type: str = "unknown"
    metric_mentions: tuple[str, ...] = ()
    dimension_slots: tuple[dict[str, Any], ...] = ()
    time_range: dict[str, Any] = field(default_factory=dict)
    ambiguous_slots: tuple[str, ...] = ()
    conflict_slots: tuple[str, ...] = ()
    subject_domain: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationIssue:
    """单个确定性问题，类别用于执行器映射到既有澄清节点。"""

    code: str
    category: Literal["rewrite", "intent", "slot", "repair"]
    clarification_slots: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationResult:
    """与 Agent、Graph 展示契约无关的统一校验结果。"""

    issues: tuple[QuestionUnderstandingValidationIssue, ...] = ()

    @property
    def reason_codes(self) -> list[str]:
        return _unique_strings([issue.code for issue in self.issues])

    @property
    def clarification_slots(self) -> list[str]:
        return _unique_strings(
            [slot for issue in self.issues for slot in issue.clarification_slots]
        )


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "DimensionRecognitionOutput",
    "DimensionSlot",
    "IntentRecognitionOutput",
    "IntentType",
    "IntentValidationOutput",
    "NaturalLanguageIntentOutputBase",
    "QuestionClassificationOutputBase",
    "QuestionRewriteOutput",
    "QuestionRewriteOutputBase",
    "QuestionRewriteProjectionOutput",
    "QuestionIntentProjectionData",
    "QuestionIntentProjectionResult",
    "QuestionUnderstandingOutcome",
    "QuestionUnderstandingOutput",
    "QuestionUnderstandingValidationData",
    "QuestionUnderstandingValidationIssue",
    "QuestionUnderstandingValidationResult",
    "RequiredSlotType",
    "TimeRange",
]
