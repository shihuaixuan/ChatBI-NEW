"""用户记忆模块公开 DTO。"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryLayer(StrEnum):
    """记忆内容层级。"""

    ATOM = "atom"
    SCENARIO = "scenario"
    PROFILE = "profile"


class MemoryType(StrEnum):
    """P0 支持的用户记忆类型。"""

    SEMANTIC_PREFERENCE = "semantic_preference"
    TERM_PREFERENCE = "term_preference"
    QUERY_SHAPE_PREFERENCE = "query_shape_preference"
    PRESENTATION_PREFERENCE = "presentation_preference"
    CORRECTION = "correction"
    NEGATIVE_PREFERENCE = "negative_preference"


class MemoryRecallVariant(StrEnum):
    """记忆召回实验分组。"""

    DISABLED = "disabled"
    CONTROL = "control"
    TREATMENT = "treatment"


class MemoryRecallComparisonDecision(StrEnum):
    """召回灰度比较建议。"""

    COLLECT_MORE_DATA = "collect_more_data"
    CONTINUE_TREATMENT = "continue_treatment"
    ROLLBACK_TREATMENT = "rollback_treatment"


class MemoryStatus(StrEnum):
    """记忆生命周期状态。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFLICTING = "conflicting"
    DISABLED = "disabled"
    EXPIRED = "expired"


class MemoryEvidenceType(StrEnum):
    """记忆证据来源。"""

    EXPLICIT_CONFIRMATION = "explicit_confirmation"
    EXPLICIT_CORRECTION = "explicit_correction"
    USER_SETTING = "user_setting"
    REPEATED_BEHAVIOR = "repeated_behavior"
    SUCCESSFUL_QUERY = "successful_query"
    MANUAL_EDIT = "manual_edit"


class MemoryCreateInput(BaseModel):
    """用户主动创建记忆的输入，不允许指定归属和状态。"""

    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdateInput(BaseModel):
    """用户修改自己记忆内容的输入。"""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryCandidateInput(BaseModel):
    """提取流程产生的单条候选记忆。"""

    model_config = ConfigDict(extra="forbid")

    layer: MemoryLayer = MemoryLayer.ATOM
    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_type: MemoryEvidenceType
    evidence_text: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=160)
    source_session_id: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0.5, ge=0, le=1)
    explicit: bool = False
    expires_at: datetime | None = None


class MemoryRecord(BaseModel):
    """Repository 与上层 Service 之间的用户记忆快照。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    oid: int
    user_id: int
    layer: MemoryLayer
    memory_type: MemoryType
    memory_key: str
    statement: str
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    evidence_count: int
    session_count: int = 0
    version: int = 1
    status: MemoryStatus
    last_confirmed_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryEvidenceRecord(BaseModel):
    """用户记忆的来源证据快照。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    memory_id: int
    oid: int
    user_id: int
    evidence_type: MemoryEvidenceType
    source_ref: str | None = None
    source_session_id: str | None = None
    evidence_text: str
    strength: float
    created_at: datetime | None = None


class MemoryUsageRecord(BaseModel):
    """一次用户记忆进入 Agent 上下文的使用记录。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    memory_id: int
    oid: int
    user_id: int
    session_id: str | None = None
    run_id: str | None = None
    stage: str
    matched_by: str
    recall_variant: MemoryRecallVariant = MemoryRecallVariant.DISABLED
    adopted: bool | None = None
    adopted_at: datetime | None = None
    created_at: datetime | None = None


class MemoryUsageSummary(BaseModel):
    """单条用户记忆的使用统计。"""

    memory_id: int
    usage_count: int
    unique_session_count: int
    unique_run_count: int
    adopted_count: int
    evaluated_count: int
    adoption_rate: float | None = None
    last_used_at: datetime | None = None


class MemoryVariantUsageMetrics(BaseModel):
    """单个召回变体的使用统计。"""

    recall_variant: MemoryRecallVariant
    usage_count: int
    unique_session_count: int
    unique_run_count: int
    adopted_count: int
    evaluated_count: int
    adoption_rate: float | None = None
    last_used_at: datetime | None = None


class MemoryRecallComparison(BaseModel):
    """控制组和实验组的用户级效果比较。"""

    control: MemoryVariantUsageMetrics | None = None
    treatment: MemoryVariantUsageMetrics | None = None
    min_evaluated_count: int
    max_adoption_drop: float
    enough_data: bool
    adoption_rate_delta: float | None = None
    recommendation: MemoryRecallComparisonDecision


class MemoryUsageMetrics(BaseModel):
    """用户范围内的记忆使用和采用统计。"""

    active_memory_count: int
    profile_count: int
    scenario_count: int
    atom_count: int
    usage_count: int
    unique_session_count: int
    unique_run_count: int
    adopted_count: int
    evaluated_count: int
    adoption_rate: float | None = None
    last_used_at: datetime | None = None
    items: list[MemoryUsageSummary] = Field(default_factory=list)
    by_variant: list[MemoryVariantUsageMetrics] = Field(default_factory=list)


class MemoryUsageAdoptionInput(BaseModel):
    """用户或评估流程对一次记忆使用的采用判定。"""

    model_config = ConfigDict(extra="forbid")

    adopted: bool


class MemoryRecallAssignment(BaseModel):
    """当前用户的稳定召回实验分组结果。"""

    recall_variant: MemoryRecallVariant
    bucket: int = Field(ge=0, lt=10000)


class MemoryEvaluationSample(BaseModel):
    """单条离线评测样本及其人工标注结果。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=160)
    recall_variant: MemoryRecallVariant | None = None
    expected_memory_ids: list[int] = Field(default_factory=list, max_length=1000)
    retrieved_memory_ids: list[int] = Field(default_factory=list, max_length=1000)
    adopted_memory_ids: list[int] = Field(default_factory=list, max_length=1000)
    rejected_memory_ids: list[int] = Field(default_factory=list, max_length=1000)


class MemoryEvaluationCaseResult(BaseModel):
    """单条离线评测样本的计算结果。"""

    case_id: str
    expected_count: int
    retrieved_count: int
    relevant_count: int
    false_positive_count: int
    adopted_count: int
    evaluated_count: int
    recall: float | None = None
    precision: float | None = None
    f1: float | None = None
    wrong_memory_rate: float | None = None
    adoption_rate: float | None = None


class MemoryEvaluationResult(BaseModel):
    """离线评测的汇总结果和单样本结果。"""

    sample_count: int
    expected_count: int
    retrieved_count: int
    relevant_count: int
    false_positive_count: int
    adopted_count: int
    evaluated_count: int
    recall: float | None = None
    precision: float | None = None
    f1: float | None = None
    wrong_memory_rate: float | None = None
    adoption_rate: float | None = None
    cases: list[MemoryEvaluationCaseResult] = Field(default_factory=list)


class MemoryEvaluationRequest(BaseModel):
    """离线评测请求，不保存原始问题和查询结果。"""

    model_config = ConfigDict(extra="forbid")

    samples: list[MemoryEvaluationSample] = Field(min_length=1, max_length=1000)


class SuccessfulQueryMemoryEvent(BaseModel):
    """成功问数事件的记忆提取输入，不包含数据集或语义资产 ID。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    intent_type: str = Field(min_length=1, max_length=64)
    query_shape: dict[str, Any] = Field(default_factory=dict)


class ClarificationMemoryEvent(BaseModel):
    """用户明确澄清或纠正事件的记忆提取输入。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    answer_text: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=160)
    source_session_id: str | None = Field(default=None, max_length=160)


class MemoryListResult(BaseModel):
    """用户记忆列表结果。"""

    items: list[MemoryRecord] = Field(default_factory=list)


class MemoryContextItem(BaseModel):
    """允许进入 Agent 请求上下文的最小记忆提示。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: int
    layer: MemoryLayer
    memory_type: MemoryType
    statement: str
    confidence: float
    evidence_count: int


class MemoryContextSnapshot(BaseModel):
    """一次问数读取到的分层用户记忆上下文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: tuple[MemoryContextItem, ...] = ()
    scenarios: tuple[MemoryContextItem, ...] = ()
    hints: tuple[MemoryContextItem, ...] = ()
    conflicts: tuple[MemoryContextItem, ...] = ()


__all__ = [
    "MemoryCandidateInput",
    "MemoryCreateInput",
    "MemoryContextItem",
    "MemoryContextSnapshot",
    "ClarificationMemoryEvent",
    "MemoryEvidenceRecord",
    "MemoryEvidenceType",
    "MemoryLayer",
    "MemoryListResult",
    "MemoryRecord",
    "MemoryStatus",
    "MemoryUsageRecord",
    "MemoryUsageAdoptionInput",
    "MemoryUsageMetrics",
    "MemoryUsageSummary",
    "MemoryEvaluationCaseResult",
    "MemoryEvaluationRequest",
    "MemoryEvaluationResult",
    "MemoryEvaluationSample",
    "MemoryRecallAssignment",
    "MemoryRecallComparison",
    "MemoryRecallComparisonDecision",
    "MemoryRecallVariant",
    "SuccessfulQueryMemoryEvent",
    "MemoryType",
    "MemoryUpdateInput",
    "MemoryVariantUsageMetrics",
]
