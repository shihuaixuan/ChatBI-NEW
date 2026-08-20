"""Research 模式的执行范围、动作、证据和运行状态契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchReason(StrEnum):
    """进入动态研究模式的结构化原因。"""

    RESULT_DRIVEN_FILTER = "result_driven_filter"
    RESULT_DRIVEN_DIMENSION = "result_driven_dimension"
    OPEN_ENDED_CAUSE = "open_ended_cause"
    DATA_DRIVEN_STOP_CONDITION = "data_driven_stop_condition"


class ResearchActionType(StrEnum):
    """Research Policy 可以选择的动作白名单。"""

    COMPARE = "compare"
    BREAKDOWN = "breakdown"
    DRILLDOWN = "drilldown"
    FILTER_FROM_RESULT = "filter_from_result"
    CONTRIBUTION = "contribution"
    VALIDATE_HYPOTHESIS = "validate_hypothesis"
    FINISH = "finish"


class ResearchHypothesisStatus(StrEnum):
    """候选假设的有限状态。"""

    PENDING = "pending"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"


class ResearchRunStatus(StrEnum):
    """Research Run 的持久化状态。"""

    INITIALIZING = "initializing"
    RUNNING = "running"
    CONCLUDING = "concluding"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ResearchFinishReason(StrEnum):
    """模型或服务端结束研究的标准原因。"""

    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    PREMISE_NOT_SUPPORTED = "premise_not_supported"
    NO_NEW_DIRECTION = "no_new_direction"
    DATA_INSUFFICIENT = "data_insufficient"
    NEEDS_CLARIFICATION = "needs_clarification"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ResearchTerminationReason(StrEnum):
    """Research Run 的服务端终止原因。"""

    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    PREMISE_NOT_SUPPORTED = "premise_not_supported"
    NO_NEW_DIRECTION = "no_new_direction"
    DATA_INSUFFICIENT = "data_insufficient"
    NEEDS_CLARIFICATION = "needs_clarification"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EXECUTION_FAILED = "execution_failed"
    PARTIAL_FAILURE = "partial_failure"
    CANCELLED = "cancelled"


class ResearchBudget(BaseModel):
    """服务端控制的 Research 硬预算。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: int = Field(default=6, gt=0, le=20)
    max_queries: int = Field(default=8, gt=0, le=50)
    max_model_calls: int = Field(default=8, gt=1, le=50)
    max_actions_per_iteration: int = Field(default=3, gt=0, le=10)
    max_duration_seconds: int = Field(default=300, gt=0, le=1800)
    max_evidence_rows: int = Field(default=20, gt=0, le=100)
    max_evidence_chars: int = Field(default=12_000, gt=0, le=100_000)


class ResearchFilterBinding(BaseModel):
    """进入研究前已经确认且不可由模型修改的筛选条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: Any
    stage: Literal["where", "having"] = "where"


class ResearchAppliedFilter(BaseModel):
    """Research 动作链中已经由服务端确定的动态筛选。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: Any
    stage: Literal["where", "having"] = "where"


class ResearchTimeBinding(BaseModel):
    """进入研究前已经绑定并归一化的时间条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1, max_length=64)
    expression: str = Field(min_length=1)
    dimension_ref: str = Field(min_length=1)
    dimension_id: int = Field(gt=0)
    normalized: dict[str, Any]

    @model_validator(mode="after")
    def validate_normalized_time(self) -> ResearchTimeBinding:
        """Research 不接受尚未解析或明确不支持的时间范围。"""

        if not self.normalized or self.normalized.get("kind") == "unsupported":
            raise ValueError("RESEARCH_TIME_NOT_NORMALIZED")
        return self


class ResearchHierarchy(BaseModel):
    """研究范围内已经治理的维度层级。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    dimension_refs: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_unique_dimensions(self) -> ResearchHierarchy:
        if len(self.dimension_refs) != len(set(self.dimension_refs)):
            raise ValueError("RESEARCH_HIERARCHY_DIMENSION_DUPLICATED")
        return self


class ResearchDriverRelationship(BaseModel):
    """目标指标与驱动指标之间的已治理分析关系。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_metric_ref: str = Field(min_length=1)
    driver_metric_ref: str = Field(min_length=1)
    relationship_type: Literal[
        "formula_component",
        "certified_driver",
        "governed_analysis_relation",
    ]
    dimension_refs: tuple[str, ...] = ()
    time_roles: tuple[str, ...] = Field(min_length=1)
    relationship_fingerprint: str = Field(min_length=1)
    status: Literal["CERTIFIED"] = "CERTIFIED"

    @model_validator(mode="after")
    def validate_relationship(self) -> ResearchDriverRelationship:
        if self.target_metric_ref == self.driver_metric_ref:
            raise ValueError("RESEARCH_DRIVER_RELATIONSHIP_SELF_REFERENCE")
        if len(self.dimension_refs) != len(set(self.dimension_refs)):
            raise ValueError("RESEARCH_DRIVER_RELATIONSHIP_DIMENSION_DUPLICATED")
        if len(self.time_roles) != len(set(self.time_roles)):
            raise ValueError("RESEARCH_DRIVER_RELATIONSHIP_TIME_ROLE_DUPLICATED")
        return self


class ResearchScope(BaseModel):
    """Research 运行期间不可扩大的授权资产范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_refs: tuple[str, ...] = ()
    target_metric_refs: tuple[str, ...] = ()
    driver_metric_refs: tuple[str, ...] = ()
    hierarchies: tuple[ResearchHierarchy, ...] = ()
    driver_relationships: tuple[ResearchDriverRelationship, ...] = ()
    allowed_filter_refs: tuple[str, ...] = ()
    contribution_metric_refs: tuple[str, ...] = ()
    contribution_dimension_refs: tuple[str, ...] = ()
    excluded_asset_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> ResearchScope:
        """范围内引用必须唯一，层级和筛选维度必须属于候选维度。"""

        for name, values in (
            ("dimension_refs", self.dimension_refs),
            ("target_metric_refs", self.target_metric_refs),
            ("driver_metric_refs", self.driver_metric_refs),
            ("contribution_metric_refs", self.contribution_metric_refs),
            ("contribution_dimension_refs", self.contribution_dimension_refs),
            ("allowed_filter_refs", self.allowed_filter_refs),
            ("excluded_asset_refs", self.excluded_asset_refs),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"RESEARCH_SCOPE_{name.upper()}_DUPLICATED")
        dimensions = set(self.dimension_refs)
        targets = set(self.target_metric_refs)
        drivers = set(self.driver_metric_refs)
        if not set(self.allowed_filter_refs) <= dimensions:
            raise ValueError("RESEARCH_SCOPE_FILTER_DIMENSION_UNKNOWN")
        if not set(self.contribution_dimension_refs) <= dimensions:
            raise ValueError("RESEARCH_SCOPE_CONTRIBUTION_DIMENSION_UNKNOWN")
        hierarchy_ids = [item.id for item in self.hierarchies]
        if len(hierarchy_ids) != len(set(hierarchy_ids)):
            raise ValueError("RESEARCH_SCOPE_HIERARCHY_ID_DUPLICATED")
        if any(
            not set(hierarchy.dimension_refs) <= dimensions
            for hierarchy in self.hierarchies
        ):
            raise ValueError("RESEARCH_SCOPE_HIERARCHY_DIMENSION_UNKNOWN")
        relationship_ids = {
            item.relationship_fingerprint for item in self.driver_relationships
        }
        if len(relationship_ids) != len(self.driver_relationships):
            raise ValueError("RESEARCH_SCOPE_DRIVER_RELATIONSHIP_DUPLICATED")
        if any(
            item.target_metric_ref not in targets
            for item in self.driver_relationships
        ):
            raise ValueError("RESEARCH_SCOPE_DRIVER_TARGET_UNKNOWN")
        if any(item.driver_metric_ref not in drivers for item in self.driver_relationships):
            raise ValueError("RESEARCH_SCOPE_DRIVER_METRIC_UNKNOWN")
        if any(
            not set(item.dimension_refs) <= dimensions
            for item in self.driver_relationships
        ):
            raise ValueError("RESEARCH_SCOPE_DRIVER_DIMENSION_UNKNOWN")
        included = dimensions | set(self.driver_metric_refs)
        if included & set(self.excluded_asset_refs):
            raise ValueError("RESEARCH_SCOPE_INCLUDED_ASSET_EXCLUDED")
        return self


class ResearchVersionSnapshot(BaseModel):
    """研究范围使用的不可变语义版本和指纹。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(gt=0)
    contract_version: int = Field(ge=0)
    schema_fingerprint: str = Field(min_length=1)
    scope_fingerprint: str = Field(min_length=1)


class ResearchRequirement(BaseModel):
    """第 5 步生成的完整 Research 执行需求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=1000)
    reason: ResearchReason
    target_metric_refs: tuple[str, ...] = Field(min_length=1)
    time_roles: tuple[str, ...] = Field(min_length=1)
    time_bindings: tuple[ResearchTimeBinding, ...] = ()
    immutable_filters: tuple[ResearchFilterBinding, ...] = ()
    scope: ResearchScope
    allowed_actions: tuple[ResearchActionType, ...] = Field(min_length=1)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    version_snapshot: ResearchVersionSnapshot

    @model_validator(mode="after")
    def validate_requirement(self) -> ResearchRequirement:
        """目标、时间、范围和动作必须形成可审计的封闭研究边界。"""

        for code, values in (
            ("TARGET_METRIC", self.target_metric_refs),
            ("TIME_ROLE", self.time_roles),
            ("ALLOWED_ACTION", self.allowed_actions),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"RESEARCH_{code}_DUPLICATED")
        binding_roles = tuple(item.role for item in self.time_bindings)
        if len(binding_roles) != len(set(binding_roles)):
            raise ValueError("RESEARCH_TIME_BINDING_ROLE_DUPLICATED")
        if self.time_bindings and set(binding_roles) != set(self.time_roles):
            raise ValueError("RESEARCH_TIME_BINDING_ROLE_MISMATCH")
        if not self.time_bindings and self.time_roles != ("single",):
            raise ValueError("RESEARCH_TIME_BINDING_REQUIRED")
        if ResearchActionType.FINISH not in self.allowed_actions:
            raise ValueError("RESEARCH_FINISH_ACTION_REQUIRED")
        if not any(
            action is not ResearchActionType.FINISH
            for action in self.allowed_actions
        ):
            raise ValueError("RESEARCH_EXECUTION_ACTION_REQUIRED")
        if not self.scope.dimension_refs and not self.scope.driver_metric_refs:
            raise ValueError("RESEARCH_SCOPE_EMPTY")
        if set(self.target_metric_refs) & set(self.scope.driver_metric_refs):
            raise ValueError("RESEARCH_TARGET_DRIVER_METRIC_CONFLICT")
        return self


class ResearchCompareAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.COMPARE] = ResearchActionType.COMPARE
    metric_refs: tuple[str, ...] = Field(min_length=1)
    time_roles: tuple[str, ...] = Field(min_length=1)


class ResearchBreakdownAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.BREAKDOWN] = ResearchActionType.BREAKDOWN
    metric_ref: str = Field(min_length=1)
    dimension_ref: str = Field(min_length=1)
    calculation: Literal["value", "difference", "growth_rate"] = "value"
    time_roles: tuple[str, ...] = Field(min_length=1)


class ResearchRowOrder(BaseModel):
    """使用逻辑血缘声明结果内的排序列。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_ref: str = Field(min_length=1)
    value_role: Literal["value", "current", "previous", "difference", "growth_rate"]


class ResearchRowSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(gt=0)
    order_by: ResearchRowOrder
    direction: Literal["asc", "desc"]


class ResearchDrilldownAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.DRILLDOWN] = ResearchActionType.DRILLDOWN
    hierarchy_id: str = Field(min_length=1)
    source_result_id: str = Field(min_length=1)
    current_dimension_ref: str = Field(min_length=1)
    next_dimension_ref: str = Field(min_length=1)
    metric_refs: tuple[str, ...] = Field(min_length=1)
    row_selector: ResearchRowSelector


class ResearchFocusedAnalysis(BaseModel):
    """从结果选择对象后允许执行的受限后续分析。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["compare", "breakdown"]
    metric_refs: tuple[str, ...] = Field(min_length=1)
    time_roles: tuple[str, ...] = Field(min_length=1)
    dimension_ref: str | None = None

    @model_validator(mode="after")
    def validate_breakdown_dimension(self) -> ResearchFocusedAnalysis:
        if self.type == "breakdown" and self.dimension_ref is None:
            raise ValueError("RESEARCH_FOCUSED_BREAKDOWN_DIMENSION_REQUIRED")
        if self.type == "compare" and self.dimension_ref is not None:
            raise ValueError("RESEARCH_FOCUSED_COMPARE_DIMENSION_NOT_ALLOWED")
        return self


class ResearchFilterFromResultAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.FILTER_FROM_RESULT] = (
        ResearchActionType.FILTER_FROM_RESULT
    )
    source_result_id: str = Field(min_length=1)
    row_selector: ResearchRowSelector
    target_dimension_ref: str = Field(min_length=1)
    analysis: ResearchFocusedAnalysis


class ResearchContributionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.CONTRIBUTION] = ResearchActionType.CONTRIBUTION
    metric_ref: str = Field(min_length=1)
    dimension_ref: str = Field(min_length=1)
    time_roles: tuple[str, str]


class ResearchValidateHypothesisAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.VALIDATE_HYPOTHESIS] = (
        ResearchActionType.VALIDATE_HYPOTHESIS
    )
    hypothesis_id: str = Field(min_length=1, max_length=128)
    metric_refs: tuple[str, ...] = Field(min_length=1)
    dimension_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ResearchFinishAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[ResearchActionType.FINISH] = ResearchActionType.FINISH
    reason: ResearchFinishReason


ResearchAction = Annotated[
    ResearchCompareAction
    | ResearchBreakdownAction
    | ResearchDrilldownAction
    | ResearchFilterFromResultAction
    | ResearchContributionAction
    | ResearchValidateHypothesisAction
    | ResearchFinishAction,
    Field(discriminator="type"),
]


class ResearchAssessment(BaseModel):
    """模型对当前证据覆盖情况的结构化评估。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_progress: Literal["none", "partial", "sufficient"]
    evidence_summary: str = Field(min_length=1, max_length=4000)
    information_gap: str | None = Field(default=None, max_length=2000)


class ResearchHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=1000)
    status: ResearchHypothesisStatus = ResearchHypothesisStatus.PENDING
    evidence_ids: tuple[str, ...] = ()


class ResearchHypothesisUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=128)
    status: ResearchHypothesisStatus
    evidence_ids: tuple[str, ...] = ()


class ResearchExecuteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["execute"] = "execute"
    actions: tuple[ResearchAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_finish_action(self) -> ResearchExecuteDecision:
        if any(action.type is ResearchActionType.FINISH for action in self.actions):
            raise ValueError("RESEARCH_EXECUTE_FINISH_ACTION_NOT_ALLOWED")
        return self


class ResearchFinishDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["finish"] = "finish"
    reason: ResearchFinishReason


ResearchDecision = Annotated[
    ResearchExecuteDecision | ResearchFinishDecision,
    Field(discriminator="type"),
]


class ResearchPolicyDecision(BaseModel):
    """Research Policy 单轮唯一允许返回的结构化结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment: ResearchAssessment
    hypothesis_updates: tuple[ResearchHypothesisUpdate, ...] = ()
    new_hypotheses: tuple[ResearchHypothesis, ...] = ()
    decision: ResearchDecision

    @model_validator(mode="after")
    def validate_hypothesis_ids(self) -> ResearchPolicyDecision:
        new_ids = [item.id for item in self.new_hypotheses]
        update_ids = [item.hypothesis_id for item in self.hypothesis_updates]
        if len(new_ids) != len(set(new_ids)):
            raise ValueError("RESEARCH_NEW_HYPOTHESIS_ID_DUPLICATED")
        if len(update_ids) != len(set(update_ids)):
            raise ValueError("RESEARCH_HYPOTHESIS_UPDATE_DUPLICATED")
        if set(new_ids) & set(update_ids):
            raise ValueError("RESEARCH_HYPOTHESIS_NEW_AND_UPDATE_CONFLICT")
        if any(
            item.status is not ResearchHypothesisStatus.PENDING
            for item in self.new_hypotheses
        ):
            raise ValueError("RESEARCH_NEW_HYPOTHESIS_MUST_BE_PENDING")
        return self


class EvidenceLogicalColumn(BaseModel):
    """证据字段的逻辑血缘，不向模型暴露物理列。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_ref: str | None = None
    dimension_ref: str | None = None
    value_role: Literal[
        "group_key",
        "value",
        "current",
        "previous",
        "difference",
        "growth_rate",
        "contribution",
    ]

    @model_validator(mode="after")
    def validate_single_asset_ref(self) -> EvidenceLogicalColumn:
        if (self.metric_ref is None) == (self.dimension_ref is None):
            raise ValueError("RESEARCH_EVIDENCE_LOGICAL_REF_INVALID")
        return self


class EvidenceStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_count: int = Field(ge=0)
    positive_count: int | None = Field(default=None, ge=0)
    negative_count: int | None = Field(default=None, ge=0)
    null_count: int | None = Field(default=None, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> EvidenceStatistics:
        counts = (
            self.positive_count,
            self.negative_count,
            self.null_count,
        )
        if any(item is not None and item > self.row_count for item in counts):
            raise ValueError("RESEARCH_EVIDENCE_COUNT_EXCEEDS_ROWS")
        return self


class EvidenceRowValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_column_index: int = Field(ge=0)
    value: Any


class EvidenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[EvidenceRowValue, ...]


class EvidenceLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    action_fingerprint: str = Field(min_length=1)


class EvidenceSnapshot(BaseModel):
    """提供给 Research Policy 的受控结果摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=1000)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    time_roles: tuple[str, ...] = ()
    logical_columns: tuple[EvidenceLogicalColumn, ...] = Field(min_length=1)
    row_order: ResearchRowOrder
    statistics: EvidenceStatistics
    top_rows: tuple[EvidenceRow, ...] = ()
    bottom_rows: tuple[EvidenceRow, ...] = ()
    lineage: EvidenceLineage
    applied_filters: tuple[ResearchAppliedFilter, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    batch_id: str | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_row_column_indexes(self) -> EvidenceSnapshot:
        column_count = len(self.logical_columns)
        if any(
            value.logical_column_index >= column_count
            for row in (*self.top_rows, *self.bottom_rows)
            for value in row.values
        ):
            raise ValueError("RESEARCH_EVIDENCE_COLUMN_INDEX_UNKNOWN")
        if not any(
            column.metric_ref == self.row_order.metric_ref
            and column.value_role == self.row_order.value_role
            for column in self.logical_columns
        ):
            raise ValueError("RESEARCH_EVIDENCE_ROW_ORDER_COLUMN_UNKNOWN")
        return self


class ResearchActionFailure(BaseModel):
    """单个 Research 动作失败时保留的结构化归属。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_fingerprint: str = Field(min_length=1)
    error_code: str = Field(min_length=1)


class ResearchIterationRecord(BaseModel):
    """一轮 Research 的追加式审计记录。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration: int = Field(ge=0)
    policy_decision_fingerprint: str = Field(min_length=1)
    action_fingerprints: tuple[str, ...] = ()
    plan_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    failed_actions: tuple[ResearchActionFailure, ...] = ()


class ResearchReportCitation(BaseModel):
    """研究报告可引用的结果证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=1000)


class ResearchReportFinding(BaseModel):
    """研究报告中的一条有证据约束的发现。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"] = "medium"
    claim_level: Literal[
        "contribution",
        "common_change",
        "correlation_clue",
        "limitation",
    ] = "correlation_clue"


class ResearchReport(BaseModel):
    """第三阶段最终输出的结构化研究报告。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=4000)
    termination_reason: ResearchTerminationReason
    findings: tuple[ResearchReportFinding, ...] = ()
    supported_hypotheses: tuple[ResearchHypothesis, ...] = ()
    weakened_hypotheses: tuple[ResearchHypothesis, ...] = ()
    inconclusive_hypotheses: tuple[ResearchHypothesis, ...] = ()
    unverified_hypotheses: tuple[ResearchHypothesis, ...] = ()
    limitations: tuple[str, ...] = ()
    citations: tuple[ResearchReportCitation, ...] = ()

    @model_validator(mode="after")
    def validate_citations(self) -> ResearchReport:
        citation_ids = {item.evidence_id for item in self.citations}
        if any(
            evidence_id not in citation_ids
            for finding in self.findings
            for evidence_id in finding.evidence_ids
        ):
            raise ValueError("RESEARCH_REPORT_FINDING_CITATION_MISSING")
        return self


class ResearchRemainingBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    iterations: int = Field(ge=0)
    queries: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)


class ResearchState(BaseModel):
    """不依赖模型上下文的追加式 Research 运行状态快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research_id: str = Field(min_length=1)
    goal: str = Field(min_length=1, max_length=1000)
    status: ResearchRunStatus = ResearchRunStatus.INITIALIZING
    iteration: int = Field(default=0, ge=0)
    evidence_ids: tuple[str, ...] = ()
    hypotheses: tuple[ResearchHypothesis, ...] = ()
    iteration_records: tuple[ResearchIterationRecord, ...] = ()
    assessment_summaries: tuple[str, ...] = ()
    covered_dimension_refs: tuple[str, ...] = ()
    covered_driver_metric_refs: tuple[str, ...] = ()
    consecutive_no_new_direction: int = Field(default=0, ge=0)
    premise_supported: bool | None = None
    executed_action_fingerprints: tuple[str, ...] = ()
    remaining_budget: ResearchRemainingBudget
    finish_reason: ResearchTerminationReason | None = None
    report: ResearchReport | None = None

    @model_validator(mode="after")
    def validate_state(self) -> ResearchState:
        for code, values in (
            ("EVIDENCE_ID", self.evidence_ids),
            ("ACTION_FINGERPRINT", self.executed_action_fingerprints),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"RESEARCH_STATE_{code}_DUPLICATED")
        hypothesis_ids = [item.id for item in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("RESEARCH_STATE_HYPOTHESIS_ID_DUPLICATED")
        terminal = self.status in {
            ResearchRunStatus.SUCCEEDED,
            ResearchRunStatus.PARTIAL,
            ResearchRunStatus.NEEDS_CLARIFICATION,
            ResearchRunStatus.FAILED,
            ResearchRunStatus.CANCELLED,
            ResearchRunStatus.BUDGET_EXHAUSTED,
        }
        if terminal != (self.finish_reason is not None):
            raise ValueError("RESEARCH_STATE_FINISH_REASON_MISMATCH")
        required_reasons = {
            ResearchRunStatus.SUCCEEDED: {
                ResearchTerminationReason.SUFFICIENT_EVIDENCE,
                ResearchTerminationReason.PREMISE_NOT_SUPPORTED,
                ResearchTerminationReason.NO_NEW_DIRECTION,
            },
            ResearchRunStatus.PARTIAL: {
                ResearchTerminationReason.PARTIAL_FAILURE,
                ResearchTerminationReason.BUDGET_EXHAUSTED,
            },
            ResearchRunStatus.NEEDS_CLARIFICATION: {
                ResearchTerminationReason.NEEDS_CLARIFICATION
            },
            ResearchRunStatus.FAILED: {
                ResearchTerminationReason.EXECUTION_FAILED,
                ResearchTerminationReason.DATA_INSUFFICIENT,
            },
            ResearchRunStatus.CANCELLED: {ResearchTerminationReason.CANCELLED},
            ResearchRunStatus.BUDGET_EXHAUSTED: {
                ResearchTerminationReason.BUDGET_EXHAUSTED
            },
        }
        if (
            self.status in required_reasons
            and self.finish_reason not in required_reasons[self.status]
        ):
            raise ValueError("RESEARCH_STATE_FINISH_REASON_INVALID")
        return self


__all__ = [
    "EvidenceSnapshot",
    "ResearchAppliedFilter",
    "ResearchActionFailure",
    "ResearchAction",
    "ResearchActionType",
    "ResearchBudget",
    "ResearchBreakdownAction",
    "ResearchCompareAction",
    "ResearchContributionAction",
    "ResearchDecision",
    "ResearchDrilldownAction",
    "ResearchFilterFromResultAction",
    "ResearchFinishAction",
    "ResearchFinishReason",
    "ResearchFinishDecision",
    "ResearchFocusedAnalysis",
    "ResearchHypothesis",
    "ResearchHypothesisStatus",
    "ResearchDriverRelationship",
    "ResearchHierarchy",
    "ResearchIterationRecord",
    "ResearchPolicyDecision",
    "ResearchReason",
    "ResearchRequirement",
    "ResearchRemainingBudget",
    "ResearchReport",
    "ResearchReportCitation",
    "ResearchReportFinding",
    "ResearchRowOrder",
    "ResearchRowSelector",
    "ResearchRunStatus",
    "ResearchScope",
    "ResearchState",
    "ResearchTerminationReason",
    "ResearchValidateHypothesisAction",
]
