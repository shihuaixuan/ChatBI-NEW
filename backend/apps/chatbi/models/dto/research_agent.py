"""Research Agent 的阶段 1契约。

本模块只描述 Research Agent 的输入边界、语义查询、工具事实、证据和运行快照。
它不导入旧 ResearchAction，也不提供把旧 Action 转换为新工具参数的适配器。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Generic, Literal, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.execution_requirement import SemanticOperation

RESEARCH_AGENT_CONTRACT_VERSION: Literal[1] = 1

_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_REF_PATTERN = re.compile(
    r"^(?:ASSET|METRIC|DIMENSION|FILTER|MODEL|HIERARCHY|RELATION|"
    r"LOGICAL_METRIC|LOGICAL_DIMENSION):[A-Za-z0-9_.:-]+$"
)
_FORBIDDEN_KEYS = {
    "sql",
    "query",
    "raw_sql",
    "table",
    "table_name",
    "column",
    "column_name",
    "physical_column",
    "physical_column_name",
    "physical_field",
    "physical_field_name",
    "database",
    "schema",
}


def _id(value: str, code: str = "RESEARCH_AGENT_ID_INVALID") -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(code)
    return value


def _passthrough_id(value: str, code: str) -> str:
    """上游治理标识（如语义层 hierarchy id）原样投影：只约束非空与长度。

    这类 id 由语义层生成，可能是纯数字（如 ``"1"``）；新契约若强加
    ``_ID_PATTERN`` 的字母开头规则，投影层将无法承载真实数据集。
    """

    if not value or len(value) > 128:
        raise ValueError(code)
    return value


def _ref(value: str, code: str = "RESEARCH_AGENT_LOGICAL_REF_INVALID") -> str:
    if not _REF_PATTERN.fullmatch(value):
        raise ValueError(code)
    return value


def _asset_model_id(ref: str) -> int | None:
    """读取受控资产引用中的模型 ID，用于校验跨模型关系。

    引用格式为 ``KIND:资产ID:模型ID``（见 requirements.py 的 ref 构造），
    模型 ID 在第三段；读错段会把同模型不同资产的引用误判为跨模型。
    """

    parts = ref.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _unique(values: Collection[str], code: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(code)


def _reject_physical_payload(value: Any, path: str = "payload") -> None:
    """阻止工具参数通过自由字典携带 SQL 或物理字段。

    语义筛选值仍然可以是任意受服务端编码的字面值；这里只检查字段名，
    不把用户的合法字符串值误判为 SQL。物理资产引用由 `_ref` 单独校验。
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS or normalized.startswith("physical_"):
                raise ValueError("RESEARCH_AGENT_PHYSICAL_PAYLOAD_FORBIDDEN")
            _reject_physical_payload(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_physical_payload(child, f"{path}[{index}]")


class _ContractModel(BaseModel):
    """新契约的统一 Pydantic 配置。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class _VersionedContractModel(_ContractModel):
    """需要跨边界传输或独立持久化的顶层契约。"""

    # 这是 Research Agent DTO 自身的协议版本，不是已发布语义 Schema 版本。
    agent_contract_version: Literal[1] = RESEARCH_AGENT_CONTRACT_VERSION


class ResearchReason(StrEnum):
    DIRECT_ANALYSIS = "direct_analysis"
    RESULT_DRIVEN_FILTER = "result_driven_filter"
    RESULT_DRIVEN_DIMENSION = "result_driven_dimension"
    OPEN_ENDED_CAUSE = "open_ended_cause"
    DATA_DRIVEN_STOP_CONDITION = "data_driven_stop_condition"


class ResearchPremiseType(StrEnum):
    METRIC_CHANGE = "metric_change"
    METRIC_ANOMALY = "metric_anomaly"
    USER_ASSERTION = "user_assertion"


class ResearchDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    STABLE = "stable"
    UNKNOWN = "unknown"


class ResearchTimeRole(StrEnum):
    """Research 查询可以使用的冻结时间角色。"""

    SINGLE = "single"
    CURRENT = "current"
    PREVIOUS = "previous"


class ResearchEvidenceLevel(StrEnum):
    GOVERNED = "governed"
    EXPLORATORY = "exploratory"


class ResearchQueryComparison(StrEnum):
    NONE = "none"
    DIFFERENCE = "difference"
    GROWTH_RATE = "growth_rate"
    SHARE = "share"
    CONTRIBUTION = "contribution"


class ResearchComputeOperation(StrEnum):
    DIFFERENCE = "difference"
    GROWTH_RATE = "growth_rate"
    SHARE = "share"
    RATIO = "ratio"
    RANKING = "ranking"
    TOPN_OTHER = "topn_other"
    CONTRIBUTION = "contribution"
    MERGE = "merge"
    RECONCILIATION = "reconciliation"


class ResearchOrderDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ToolObservationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolFailureStage(StrEnum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    PLANNING = "planning"
    PROOF = "proof"
    COMPILATION = "compilation"
    EXECUTION = "execution"
    PROJECTION = "projection"
    PERSISTENCE = "persistence"
    BUDGET = "budget"


class ToolErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    EVIDENCE_REFERENCE_INVALID = "EVIDENCE_REFERENCE_INVALID"
    SCOPE_DENIED = "SCOPE_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    SEMANTIC_PLAN_REJECTED = "SEMANTIC_PLAN_REJECTED"
    SQL_COMPILE_FAILED = "SQL_COMPILE_FAILED"
    SQL_VALIDATION_FAILED = "SQL_VALIDATION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EMPTY_RESULT = "EMPTY_RESULT"
    RESULT_CONTRACT_FAILED = "RESULT_CONTRACT_FAILED"
    FANOUT_DETECTED = "FANOUT_DETECTED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    RESULT_STORE_FAILED = "RESULT_STORE_FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class ResearchRunStatus(StrEnum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchCompletionReason(StrEnum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    PREMISE_NOT_SUPPORTED = "premise_not_supported"
    NO_NEW_DIRECTION = "no_new_direction"
    DATA_INSUFFICIENT = "data_insufficient"
    NEEDS_CLARIFICATION = "needs_clarification"
    EXECUTION_FAILED = "execution_failed"
    PARTIAL_FAILURE = "partial_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


class SemanticAssessmentStatus(StrEnum):
    """模型对当前 Evidence 内容充分性的四态判断。"""

    ANSWERABLE = "answerable"
    EXPLICIT_GAP = "explicit_gap"
    NO_NEW_DIRECTION = "no_new_direction"
    DATA_INSUFFICIENT = "data_insufficient"


ResearchCompletionStatus = Literal[
    "succeeded", "partial", "needs_clarification", "failed", "cancelled"
]

RESEARCH_COMPLETION_STATUS_BY_REASON: dict[
    ResearchCompletionReason, ResearchCompletionStatus
] = {
    ResearchCompletionReason.SUFFICIENT_EVIDENCE: "succeeded",
    ResearchCompletionReason.PREMISE_NOT_SUPPORTED: "succeeded",
    ResearchCompletionReason.NO_NEW_DIRECTION: "succeeded",
    ResearchCompletionReason.PARTIAL_FAILURE: "partial",
    ResearchCompletionReason.BUDGET_EXHAUSTED: "partial",
    ResearchCompletionReason.NEEDS_CLARIFICATION: "needs_clarification",
    ResearchCompletionReason.EXECUTION_FAILED: "failed",
    ResearchCompletionReason.DATA_INSUFFICIENT: "failed",
    ResearchCompletionReason.CANCELLED: "cancelled",
}


class ResearchClaimLevel(StrEnum):
    CONTRIBUTION = "contribution"
    COMMON_CHANGE = "common_change"
    CORRELATION_CLUE = "correlation_clue"
    LIMITATION = "limitation"


class ResearchVersionSnapshot(_ContractModel):
    """运行期间不能改变的语义版本、契约版本和权限范围指纹。"""

    schema_version: int = Field(gt=0)
    contract_version: int = Field(gt=0)
    schema_fingerprint: str = Field(min_length=1, max_length=256)
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    permission_fingerprint: str = Field(min_length=1, max_length=256)


class ResearchBudget(_ContractModel):
    max_iterations: int = Field(default=8, gt=0, le=20)
    max_queries: int = Field(default=8, gt=0, le=50)
    max_model_calls: int = Field(default=8, gt=0, le=50)
    max_duration_seconds: int = Field(default=300, gt=0, le=1800)
    max_evidence_rows: int = Field(default=20, gt=0, le=100)
    max_evidence_chars: int = Field(default=12_000, gt=0, le=100_000)


class ResearchTimeBinding(_ContractModel):
    role: ResearchTimeRole
    expression: str = Field(min_length=1, max_length=256)
    dimension_ref: str = Field(min_length=1)
    normalized: dict[str, Any]

    @model_validator(mode="after")
    def validate_time(self) -> ResearchTimeBinding:
        _ref(self.dimension_ref, "RESEARCH_AGENT_TIME_DIMENSION_REF_INVALID")
        if not self.dimension_ref.startswith("DIMENSION:"):
            raise ValueError("RESEARCH_AGENT_TIME_DIMENSION_REF_INVALID")
        _reject_physical_payload(self.normalized)
        if not self.normalized or self.normalized.get("kind") == "unsupported":
            raise ValueError("RESEARCH_AGENT_TIME_NOT_NORMALIZED")
        return self


class ResearchImmutableFilter(_ContractModel):
    """用户已经确认的筛选；replan 不得修改。"""

    target_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: Any

    @model_validator(mode="after")
    def validate_filter(self) -> ResearchImmutableFilter:
        _ref(self.target_ref, "RESEARCH_AGENT_FILTER_REF_INVALID")
        _reject_physical_payload(self.value)
        return self


class ResearchHierarchy(_ContractModel):
    """已治理的相邻维度层级，顺序就是允许的下钻顺序。"""

    hierarchy_id: str = Field(min_length=1, max_length=128)
    dimension_refs: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> ResearchHierarchy:
        _passthrough_id(self.hierarchy_id, "RESEARCH_AGENT_HIERARCHY_ID_INVALID")
        _unique(
            self.dimension_refs,
            "RESEARCH_AGENT_HIERARCHY_DIMENSION_DUPLICATED",
        )
        for ref in self.dimension_refs:
            _ref(ref, "RESEARCH_AGENT_HIERARCHY_DIMENSION_REF_INVALID")
        return self


class ResearchDriverRelationship(_ContractModel):
    """目标指标与驱动指标之间的已发布治理关系。"""

    target_metric_ref: str = Field(min_length=1)
    driver_metric_ref: str = Field(min_length=1)
    component_metric_refs: tuple[str, ...] = ()
    relationship_type: Literal[
        "formula_component",
        "certified_driver",
        "governed_analysis_relation",
    ]
    validation_method: Literal[
        "SAME_DIRECTION",
        "OPPOSITE_DIRECTION",
        "FORMULA_RECONCILIATION",
    ] = "SAME_DIRECTION"
    expected_direction: Literal["POSITIVE", "NEGATIVE", "UNKNOWN"] = "UNKNOWN"
    formula_definition: dict[str, Any] | None = None
    dimension_refs: tuple[str, ...] = ()
    dimension_refs_by_model: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    relation_path: tuple[int, ...] = ()
    time_roles: tuple[ResearchTimeRole, ...] = Field(min_length=1)
    relationship_fingerprint: str = Field(min_length=1, max_length=256)
    status: Literal["CERTIFIED"] = "CERTIFIED"

    @model_validator(mode="after")
    def validate_relationship(self) -> ResearchDriverRelationship:
        _ref(self.target_metric_ref, "RESEARCH_AGENT_DRIVER_TARGET_REF_INVALID")
        _ref(self.driver_metric_ref, "RESEARCH_AGENT_DRIVER_METRIC_REF_INVALID")
        if self.target_metric_ref == self.driver_metric_ref:
            raise ValueError("RESEARCH_AGENT_DRIVER_SELF_REFERENCE")
        _unique(
            self.component_metric_refs,
            "RESEARCH_AGENT_DRIVER_COMPONENT_DUPLICATED",
        )
        for ref in self.component_metric_refs:
            _ref(ref, "RESEARCH_AGENT_DRIVER_COMPONENT_REF_INVALID")
        if self.relationship_type == "formula_component":
            if not self.component_metric_refs:
                raise ValueError("RESEARCH_AGENT_FORMULA_COMPONENT_REQUIRED")
            if self.driver_metric_ref not in self.component_metric_refs:
                raise ValueError("RESEARCH_AGENT_FORMULA_DRIVER_NOT_COMPONENT")
        elif self.component_metric_refs:
            raise ValueError("RESEARCH_AGENT_NON_FORMULA_COMPONENT_FORBIDDEN")
        if self.validation_method == "FORMULA_RECONCILIATION":
            if self.expected_direction != "UNKNOWN":
                raise ValueError("RESEARCH_AGENT_FORMULA_DIRECTION_MUST_BE_UNKNOWN")
        elif (
            self.validation_method == "SAME_DIRECTION"
            and self.expected_direction == "NEGATIVE"
        ) or (
            self.validation_method == "OPPOSITE_DIRECTION"
            and self.expected_direction == "POSITIVE"
        ):
            raise ValueError("RESEARCH_AGENT_DRIVER_DIRECTION_CONFLICT")
        _unique(
            self.dimension_refs,
            "RESEARCH_AGENT_DRIVER_DIMENSION_DUPLICATED",
        )
        for ref in self.dimension_refs:
            _ref(ref, "RESEARCH_AGENT_DRIVER_DIMENSION_REF_INVALID")
        for model_id, refs in self.dimension_refs_by_model.items():
            if not str(model_id).isdigit() or not refs:
                raise ValueError("RESEARCH_AGENT_DRIVER_MODEL_DIMENSIONS_INVALID")
            _unique(refs, "RESEARCH_AGENT_DRIVER_MODEL_DIMENSIONS_DUPLICATED")
            for ref in refs:
                if not ref.startswith("DIMENSION:"):
                    raise ValueError("RESEARCH_AGENT_DRIVER_MODEL_DIMENSIONS_INVALID")
                _ref(ref, "RESEARCH_AGENT_DRIVER_MODEL_DIMENSIONS_INVALID")
                if _asset_model_id(ref) != int(model_id):
                    raise ValueError("RESEARCH_AGENT_DRIVER_MODEL_DIMENSIONS_INVALID")
        if any(item <= 0 for item in self.relation_path):
            raise ValueError("RESEARCH_AGENT_DRIVER_RELATION_PATH_INVALID")
        target_model = _asset_model_id(self.target_metric_ref)
        driver_model = _asset_model_id(self.driver_metric_ref)
        if target_model is not None and driver_model is not None and target_model != driver_model:
            if self.relationship_type == "formula_component" or not self.relation_path:
                raise ValueError("RESEARCH_AGENT_CROSS_MODEL_RELATION_PATH_REQUIRED")
            if set(self.dimension_refs_by_model) != {
                str(target_model),
                str(driver_model),
            }:
                raise ValueError("RESEARCH_AGENT_CROSS_MODEL_DIMENSION_MAPPING_REQUIRED")
        _unique(self.time_roles, "RESEARCH_AGENT_DRIVER_TIME_ROLE_DUPLICATED")
        return self


class ResearchScope(_ContractModel):
    """Research Run 启动时冻结的候选资产和权限范围。"""

    target_metric_refs: tuple[str, ...] = Field(min_length=1)
    dimension_refs: tuple[str, ...] = ()
    driver_metric_refs: tuple[str, ...] = ()
    allowed_filter_refs: tuple[str, ...] = ()
    hierarchies: tuple[ResearchHierarchy, ...] = ()
    driver_relationships: tuple[ResearchDriverRelationship, ...] = ()
    contribution_metric_refs: tuple[str, ...] = ()
    contribution_dimension_refs: tuple[str, ...] = ()
    contribution_tolerance: float = Field(default=1e-6, ge=0)
    excluded_asset_refs: tuple[str, ...] = ()
    tenant_scope: str = Field(min_length=1, max_length=256)
    dataset_ref: str = Field(min_length=1)
    scope_fingerprint: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_scope(self) -> ResearchScope:
        for name, values in (
            ("target_metric_refs", self.target_metric_refs),
            ("dimension_refs", self.dimension_refs),
            ("driver_metric_refs", self.driver_metric_refs),
            ("allowed_filter_refs", self.allowed_filter_refs),
            ("contribution_dimension_refs", self.contribution_dimension_refs),
            ("contribution_metric_refs", self.contribution_metric_refs),
            ("excluded_asset_refs", self.excluded_asset_refs),
        ):
            _unique(values, f"RESEARCH_AGENT_SCOPE_{name.upper()}_DUPLICATED")
            for value in values:
                _ref(value, "RESEARCH_AGENT_SCOPE_REF_INVALID")
        dimension_refs = set(self.dimension_refs)
        if not set(self.allowed_filter_refs) <= dimension_refs:
            raise ValueError("RESEARCH_AGENT_SCOPE_FILTER_OUT_OF_SCOPE")
        if not set(self.contribution_dimension_refs) <= dimension_refs:
            raise ValueError("RESEARCH_AGENT_SCOPE_CONTRIBUTION_DIMENSION_OUT_OF_SCOPE")
        hierarchy_ids = tuple(item.hierarchy_id for item in self.hierarchies)
        _unique(hierarchy_ids, "RESEARCH_AGENT_HIERARCHY_ID_DUPLICATED")
        for hierarchy in self.hierarchies:
            if not set(hierarchy.dimension_refs) <= dimension_refs:
                raise ValueError("RESEARCH_AGENT_HIERARCHY_DIMENSION_OUT_OF_SCOPE")
        metric_refs = set(self.target_metric_refs) | set(self.driver_metric_refs)
        if not set(self.contribution_metric_refs) <= metric_refs:
            raise ValueError("RESEARCH_AGENT_SCOPE_CONTRIBUTION_METRIC_OUT_OF_SCOPE")
        for relationship in self.driver_relationships:
            if relationship.target_metric_ref not in set(self.target_metric_refs):
                raise ValueError("RESEARCH_AGENT_DRIVER_TARGET_OUT_OF_SCOPE")
            if not {
                relationship.driver_metric_ref,
                *relationship.component_metric_refs,
            } <= set(self.driver_metric_refs):
                raise ValueError("RESEARCH_AGENT_DRIVER_METRIC_OUT_OF_SCOPE")
            if not set(relationship.dimension_refs) <= dimension_refs:
                raise ValueError("RESEARCH_AGENT_DRIVER_DIMENSION_OUT_OF_SCOPE")
            if not {
                ref
                for refs in relationship.dimension_refs_by_model.values()
                for ref in refs
            } <= dimension_refs:
                raise ValueError("RESEARCH_AGENT_DRIVER_MODEL_DIMENSION_OUT_OF_SCOPE")
        fingerprints = [
            item.relationship_fingerprint for item in self.driver_relationships
        ]
        _unique(fingerprints, "RESEARCH_AGENT_DRIVER_RELATIONSHIP_DUPLICATED")
        included = dimension_refs | metric_refs
        if included & set(self.excluded_asset_refs):
            raise ValueError("RESEARCH_AGENT_SCOPE_EXCLUDED_ASSET_INCLUDED")
        _ref(self.dataset_ref, "RESEARCH_AGENT_DATASET_REF_INVALID")
        return self


class ResearchPremise(_ContractModel):
    premise_type: ResearchPremiseType = Field(
        validation_alias=AliasChoices("premise_type", "type")
    )
    metric_ref: str = Field(min_length=1)
    expected_direction: ResearchDirection = ResearchDirection.UNKNOWN
    time_roles: tuple[ResearchTimeRole, ...] = Field(min_length=1)
    statement: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_premise(self) -> ResearchPremise:
        _ref(self.metric_ref, "RESEARCH_AGENT_PREMISE_METRIC_REF_INVALID")
        _unique(self.time_roles, "RESEARCH_AGENT_PREMISE_TIME_ROLE_DUPLICATED")
        return self

    @property
    def type(self) -> ResearchPremiseType:
        """兼容契约示例中的 type 命名，序列化字段仍固定为 premise_type。"""

        return self.premise_type


class ResearchEvidenceRequirement(_ContractModel):
    """完成目标所需的证据类型，不规定必须调用哪个工具。"""

    requirement_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "premise_confirmation",
        "dimension_or_driver_analysis",
        "claim_support",
        "counter_evidence",
        "reconciliation",
    ]
    description: str = Field(min_length=1, max_length=1000)
    required_asset_refs: tuple[str, ...] = ()
    minimum_count: int = Field(default=1, gt=0, le=20)
    minimum_claim_level: ResearchClaimLevel = ResearchClaimLevel.CORRELATION_CLUE

    @model_validator(mode="after")
    def validate_requirement(self) -> ResearchEvidenceRequirement:
        _id(self.requirement_id, "RESEARCH_AGENT_EVIDENCE_REQUIREMENT_ID_INVALID")
        _unique(self.required_asset_refs, "RESEARCH_AGENT_EVIDENCE_REQUIREMENT_REF_DUPLICATED")
        for value in self.required_asset_refs:
            _ref(value, "RESEARCH_AGENT_EVIDENCE_REQUIREMENT_REF_INVALID")
        return self


class StructuralCoverageGap(_ContractModel):
    """服务端确定的一条最低结构覆盖缺口。"""

    requirement_id: str | None = Field(default=None, max_length=128)
    kind: str = Field(min_length=1, max_length=128)
    missing_count: int = Field(gt=0, le=1000)
    message: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_gap(self) -> StructuralCoverageGap:
        if self.requirement_id is not None:
            _id(self.requirement_id, "RESEARCH_AGENT_COVERAGE_REQUIREMENT_ID_INVALID")
        return self


class StructuralCoverage(_ContractModel):
    """服务端生成的最低结构覆盖结果，不代表内容已经足够。"""

    minimum_requirements_met: bool
    premise_handled: bool
    core_supported: bool
    covered_requirements: tuple[str, ...] = ()
    missing_requirements: tuple[StructuralCoverageGap, ...] = ()
    invalid_evidence_refs: tuple[str, ...] = ()
    target_metric_coverage: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_coverage(self) -> StructuralCoverage:
        _unique(
            self.covered_requirements,
            "RESEARCH_AGENT_COVERED_REQUIREMENT_DUPLICATED",
        )
        _unique(
            self.invalid_evidence_refs,
            "RESEARCH_AGENT_INVALID_EVIDENCE_REF_DUPLICATED",
        )
        if self.minimum_requirements_met != (
            not self.missing_requirements and not self.invalid_evidence_refs
        ):
            raise ValueError("RESEARCH_AGENT_STRUCTURAL_COVERAGE_STATE_INVALID")
        if any(value < 0 for value in self.target_metric_coverage.values()):
            raise ValueError("RESEARCH_AGENT_TARGET_COVERAGE_INVALID")
        return self

    def gap_messages(self) -> tuple[str, ...]:
        return tuple(item.message for item in self.missing_requirements)


class ResearchAgentRequirement(_VersionedContractModel):
    """Research Agent 的冻结输入；不包含 allowed_actions。"""

    run_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1000)
    reason: ResearchReason
    target_metric_refs: tuple[str, ...] = Field(min_length=1)
    premise_to_verify: ResearchPremise | None = None
    time_bindings: tuple[ResearchTimeBinding, ...] = ()
    time_bindings_by_model: dict[str, tuple[ResearchTimeBinding, ...]] = Field(
        default_factory=dict
    )
    immutable_filters: tuple[ResearchImmutableFilter, ...] = ()
    scope: ResearchScope
    evidence_requirements: tuple[ResearchEvidenceRequirement, ...] = Field(min_length=1)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    version_snapshot: ResearchVersionSnapshot
    operations: tuple[SemanticOperation, ...] = ()

    @model_validator(mode="after")
    def validate_requirement(self) -> ResearchAgentRequirement:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _unique(self.target_metric_refs, "RESEARCH_AGENT_TARGET_METRIC_DUPLICATED")
        for ref in self.target_metric_refs:
            _ref(ref, "RESEARCH_AGENT_TARGET_METRIC_REF_INVALID")
        if not set(self.target_metric_refs) <= set(self.scope.target_metric_refs):
            raise ValueError("RESEARCH_AGENT_TARGET_METRIC_OUT_OF_SCOPE")
        roles = tuple(item.role for item in self.time_bindings)
        _unique(roles, "RESEARCH_AGENT_TIME_ROLE_DUPLICATED")
        if any(
            item.dimension_ref not in set(self.scope.dimension_refs)
            for item in self.time_bindings
        ):
            raise ValueError("RESEARCH_AGENT_TIME_DIMENSION_OUT_OF_SCOPE")
        scope_model_ids = {
            resolved_id
            for ref in (*self.scope.target_metric_refs, *self.scope.driver_metric_refs)
            if (resolved_id := _asset_model_id(ref)) is not None
        }
        for model_key, bindings in self.time_bindings_by_model.items():
            if not str(model_key).isdigit() or int(model_key) <= 0:
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_INVALID")
            resolved_model_id = int(model_key)
            if resolved_model_id not in scope_model_ids:
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_OUT_OF_SCOPE")
            model_roles = tuple(item.role for item in bindings)
            if len(model_roles) != len(set(model_roles)):
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_ROLE_DUPLICATED")
            if set(model_roles) != set(roles):
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_ROLE_MISMATCH")
            if any(
                item.dimension_ref not in set(self.scope.dimension_refs)
                for item in bindings
            ):
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_DIMENSION_OUT_OF_SCOPE")
            if any(
                _asset_model_id(item.dimension_ref) != resolved_model_id
                for item in bindings
            ):
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_DIMENSION_MISMATCH")
        _unique(
            tuple(item.target_ref for item in self.immutable_filters),
            "RESEARCH_AGENT_IMMUTABLE_FILTER_DUPLICATED",
        )
        for item in self.immutable_filters:
            if item.target_ref not in self.scope.allowed_filter_refs:
                raise ValueError("RESEARCH_AGENT_IMMUTABLE_FILTER_OUT_OF_SCOPE")
        if self.premise_to_verify is not None and (
            self.premise_to_verify.metric_ref not in self.target_metric_refs
        ):
            raise ValueError("RESEARCH_AGENT_PREMISE_METRIC_OUT_OF_SCOPE")
        if self.premise_to_verify is not None and not set(
            self.premise_to_verify.time_roles
        ) <= set(roles):
            raise ValueError("RESEARCH_AGENT_PREMISE_TIME_ROLE_OUT_OF_SCOPE")
        requirement_ids = [item.requirement_id for item in self.evidence_requirements]
        _unique(requirement_ids, "RESEARCH_AGENT_EVIDENCE_REQUIREMENT_DUPLICATED")
        scope_refs = (
            set(self.scope.target_metric_refs)
            | set(self.scope.dimension_refs)
            | set(self.scope.driver_metric_refs)
            | set(self.scope.allowed_filter_refs)
            | set(self.scope.contribution_metric_refs)
            | set(self.scope.contribution_dimension_refs)
        )
        for requirement in self.evidence_requirements:
            if not set(requirement.required_asset_refs) <= scope_refs:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_REQUIREMENT_OUT_OF_SCOPE")
        operation_refs = {
            item.target_ref for item in self.operations if item.target_ref is not None
        }
        if not operation_refs <= scope_refs:
            raise ValueError("RESEARCH_AGENT_OPERATION_REF_OUT_OF_SCOPE")
        time_group_count = sum(
            1
            for item in self.operations
            if item.type == "group" and item.time_grain is not None
        )
        if time_group_count > 1:
            raise ValueError("RESEARCH_AGENT_TIME_GROUP_DUPLICATED")
        if self.version_snapshot.scope_fingerprint != self.scope.scope_fingerprint:
            raise ValueError("RESEARCH_AGENT_SCOPE_FINGERPRINT_MISMATCH")
        return self

    def validate_query(
        self,
        query: ResearchSemanticQuery,
        evidence: Collection[ResearchEvidence] = (),
    ) -> None:
        """校验模型提交的 Semantic Query 是否仍在冻结 WHAT 和 Scope 内。"""

        if query.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_QUERY_RUN_MISMATCH")
        if query.scope_fingerprint != self.scope.scope_fingerprint:
            raise ValueError("RESEARCH_AGENT_QUERY_SCOPE_MISMATCH")
        if query.version_snapshot != self.version_snapshot:
            raise ValueError("RESEARCH_AGENT_QUERY_VERSION_MISMATCH")
        frozen_time_roles = {item.role for item in self.time_bindings}
        # 无显式时间条件仍是一条合法的单期查询，只是不向执行器下发时间过滤。
        if not frozen_time_roles:
            frozen_time_roles = {ResearchTimeRole.SINGLE}
        query_time_roles = set(query.time_ranges)
        if not query_time_roles or not query_time_roles <= frozen_time_roles:
            raise ValueError("RESEARCH_AGENT_QUERY_TIME_BINDING_CHANGED")
        if query.comparison in {
            ResearchQueryComparison.DIFFERENCE,
            ResearchQueryComparison.GROWTH_RATE,
            ResearchQueryComparison.CONTRIBUTION,
        } and not {
            ResearchTimeRole.CURRENT,
            ResearchTimeRole.PREVIOUS,
        } <= query_time_roles:
            raise ValueError("RESEARCH_AGENT_COMPARISON_TIME_ROLES_REQUIRED")
        query_filters = {item.target_ref: item for item in query.filters}
        for immutable_filter in self.immutable_filters:
            query_filter = query_filters.get(immutable_filter.target_ref)
            if query_filter is None:
                raise ValueError("RESEARCH_AGENT_IMMUTABLE_FILTER_MISSING")
            if (
                query_filter.operator != immutable_filter.operator
                or query_filter.value != immutable_filter.value
            ):
                raise ValueError("RESEARCH_AGENT_IMMUTABLE_FILTER_CHANGED")
        query.validate_scope(self.scope, evidence)


def validate_plan_required_operations(
    requirement: ResearchAgentRequirement,
    queries: Sequence[ResearchSemanticQuery],
    evidences: Sequence[ResearchEvidence] = (),
) -> None:
    """整份计划联合已有 Evidence 覆盖冻结结果操作即可；单条查询允许分解。

    逐查询强制会禁止"总量确认前提 + 分组归因下钻"这类自然分解，
    迫使每条查询都携带全部分组维度。重规划时，已经由上一轮 Evidence
    实际执行的操作应视为已覆盖，当前计划只需补齐剩余操作。内容层面的
    最终缺口仍由 finish 前的结构覆盖评估（Evidence 实际 operations）兜底。
    """

    dimensions: set[str] = set()
    time_grains: set[str] = set()
    order_keys: set[tuple[str, str]] = set()
    limits: set[int] = set()
    for evidence in evidences:
        for operation in evidence.operations:
            if operation.type == "group":
                if operation.time_grain is not None:
                    time_grains.add(operation.time_grain)
                elif operation.target_ref is not None:
                    dimensions.add(operation.target_ref)
            elif operation.type == "sort":
                if operation.target_ref is not None and operation.direction is not None:
                    order_keys.add((operation.target_ref, operation.direction))
            elif operation.type == "limit" and operation.value is not None:
                limits.add(operation.value)
    for query in queries:
        dimensions |= set(query.dimensions)
        if query.time_grain is not None:
            time_grains.add(query.time_grain)
        order_keys |= {(item.ref, item.direction.value) for item in query.order}
        limits.add(query.limit)
    missing_operations: list[str] = []
    for operation in requirement.operations:
        if operation.type == "group":
            if operation.time_grain is not None:
                completed = operation.time_grain in time_grains
            else:
                completed = operation.target_ref in dimensions
        elif operation.type == "sort":
            completed = (operation.target_ref, operation.direction) in order_keys
        elif operation.type == "limit":
            completed = operation.value in limits
        else:
            # 计算操作可以由 compute_evidence 在查询之后完成，不能在此处强制。
            continue
        if not completed:
            missing_operations.append(operation.type)
    if missing_operations:
        missing = ",".join(dict.fromkeys(missing_operations))
        raise ValueError(f"RESEARCH_AGENT_QUERY_OPERATION_MISSING:{missing}")


class ResearchLiteralFilter(_ContractModel):
    target_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: Any

    @model_validator(mode="after")
    def validate_filter(self) -> ResearchLiteralFilter:
        _ref(self.target_ref, "RESEARCH_AGENT_QUERY_FILTER_REF_INVALID")
        _reject_physical_payload(self.value)
        return self


class ResearchRowSelector(_ContractModel):
    """只能按受控行顺序选择结果，不能携带物理值或 SQL。"""

    rank: int = Field(gt=0, le=1000)
    direction: ResearchOrderDirection = ResearchOrderDirection.ASC
    order_by: str | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> ResearchRowSelector:
        if self.order_by is not None:
            _ref(self.order_by, "RESEARCH_AGENT_ROW_ORDER_REF_INVALID")
        return self


class ResearchEvidenceValueRef(_ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=128)
    target_ref: str = Field(min_length=1)
    column_ref: str = Field(min_length=1)
    row_selector: ResearchRowSelector

    @model_validator(mode="after")
    def validate_value_ref(self) -> ResearchEvidenceValueRef:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _id(self.evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _ref(self.target_ref, "RESEARCH_AGENT_EVIDENCE_TARGET_REF_INVALID")
        _ref(self.column_ref, "RESEARCH_AGENT_EVIDENCE_COLUMN_REF_INVALID")
        return self

    def validate_against(self, evidence: ResearchEvidence) -> None:
        if evidence.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        if evidence.evidence_id != self.evidence_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        if self.column_ref not in {
            item.asset_ref for item in evidence.logical_columns
        }:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_COLUMN_NOT_FOUND")


class ResearchOrder(_ContractModel):
    ref: str = Field(min_length=1)
    direction: ResearchOrderDirection = ResearchOrderDirection.DESC
    value_role: Literal[
        "value",
        "current",
        "previous",
        "difference",
        "growth_rate",
        "share",
        "contribution",
    ] = "value"

    @model_validator(mode="after")
    def validate_order(self) -> ResearchOrder:
        _ref(self.ref, "RESEARCH_AGENT_ORDER_REF_INVALID")
        return self


class ResearchDrilldownSpec(_ContractModel):
    """沿 Scope 中已发布层级向相邻下一层下钻。"""

    hierarchy_id: str = Field(min_length=1, max_length=128)
    source_evidence_id: str = Field(min_length=1, max_length=128)
    current_dimension_ref: str = Field(min_length=1)
    next_dimension_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_drilldown(self) -> ResearchDrilldownSpec:
        # 与 Scope 层级的透传规则一致：模型回显的 hierarchy_id 必须能匹配真实数据集。
        _passthrough_id(self.hierarchy_id, "RESEARCH_AGENT_HIERARCHY_ID_INVALID")
        _id(self.source_evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _ref(self.current_dimension_ref, "RESEARCH_AGENT_DRILLDOWN_CURRENT_REF_INVALID")
        _ref(self.next_dimension_ref, "RESEARCH_AGENT_DRILLDOWN_NEXT_REF_INVALID")
        if self.current_dimension_ref == self.next_dimension_ref:
            raise ValueError("RESEARCH_AGENT_DRILLDOWN_SAME_DIMENSION")
        return self


class ResearchSemanticQuery(_VersionedContractModel):
    """所有比较、分解、下钻和结果筛选共用的声明式查询参数。"""

    run_id: str = Field(min_length=1, max_length=128)
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    version_snapshot: ResearchVersionSnapshot
    metrics: tuple[str, ...] = Field(min_length=1)
    dimensions: tuple[str, ...] = ()
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    time_ranges: tuple[ResearchTimeRole, ...] = Field(min_length=1)
    filters: tuple[ResearchLiteralFilter, ...] = ()
    evidence_value_filters: tuple[ResearchEvidenceValueRef, ...] = ()
    comparison: ResearchQueryComparison = ResearchQueryComparison.NONE
    analysis: Literal[
        "compare",
        "breakdown",
        "drilldown",
        "filter_from_result",
        "contribution",
        "exploration",
    ] = "exploration"
    drilldown: ResearchDrilldownSpec | None = None
    order: tuple[ResearchOrder, ...] = ()
    limit: int = Field(default=100, gt=0, le=1000)
    purpose: str = Field(min_length=1, max_length=1000)
    hypothesis_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_query(self) -> ResearchSemanticQuery:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _unique(self.metrics, "RESEARCH_AGENT_QUERY_METRIC_DUPLICATED")
        _unique(self.dimensions, "RESEARCH_AGENT_QUERY_DIMENSION_DUPLICATED")
        _unique(self.time_ranges, "RESEARCH_AGENT_QUERY_TIME_ROLE_DUPLICATED")
        _unique(
            tuple(item.target_ref for item in self.filters),
            "RESEARCH_AGENT_QUERY_FILTER_DUPLICATED",
        )
        _unique(self.hypothesis_ids, "RESEARCH_AGENT_QUERY_HYPOTHESIS_DUPLICATED")
        for ref in (*self.metrics, *self.dimensions):
            _ref(ref, "RESEARCH_AGENT_QUERY_LOGICAL_REF_INVALID")
        for hypothesis_id in self.hypothesis_ids:
            _id(hypothesis_id, "RESEARCH_AGENT_HYPOTHESIS_ID_INVALID")
        if self.analysis == "breakdown" and not self.dimensions:
            raise ValueError("RESEARCH_AGENT_BREAKDOWN_DIMENSION_REQUIRED")
        if self.analysis == "drilldown" and self.drilldown is None:
            raise ValueError("RESEARCH_AGENT_DRILLDOWN_SPEC_REQUIRED")
        if self.analysis == "filter_from_result" and not self.evidence_value_filters:
            raise ValueError("RESEARCH_AGENT_RESULT_FILTER_EVIDENCE_REQUIRED")
        if self.analysis == "contribution" and (
            self.comparison is not ResearchQueryComparison.CONTRIBUTION
        ):
            raise ValueError("RESEARCH_AGENT_CONTRIBUTION_COMPARISON_REQUIRED")
        return self

    def validate_scope(
        self,
        scope: ResearchScope,
        evidence: Collection[ResearchEvidence] = (),
    ) -> None:
        allowed_metrics = set(scope.target_metric_refs) | set(scope.driver_metric_refs)
        evidence_by_id = {item.evidence_id: item for item in evidence}
        if not set(self.metrics) <= allowed_metrics:
            raise ValueError("RESEARCH_AGENT_QUERY_METRIC_OUT_OF_SCOPE")
        if not set(self.dimensions) <= set(scope.dimension_refs):
            raise ValueError("RESEARCH_AGENT_QUERY_DIMENSION_OUT_OF_SCOPE")
        if any(
            item.target_ref not in set(scope.allowed_filter_refs)
            for item in self.filters
        ):
            raise ValueError("RESEARCH_AGENT_QUERY_FILTER_OUT_OF_SCOPE")
        if self.analysis == "contribution":
            if not set(self.metrics) <= set(scope.contribution_metric_refs):
                raise ValueError("RESEARCH_AGENT_CONTRIBUTION_METRIC_OUT_OF_SCOPE")
            if not set(self.dimensions) <= set(scope.contribution_dimension_refs):
                raise ValueError(
                    "RESEARCH_AGENT_CONTRIBUTION_DIMENSION_OUT_OF_SCOPE"
                )
        if self.drilldown is not None:
            hierarchy = next(
                (
                    item
                    for item in scope.hierarchies
                    if item.hierarchy_id == self.drilldown.hierarchy_id
                ),
                None,
            )
            if hierarchy is None:
                raise ValueError("RESEARCH_AGENT_DRILLDOWN_HIERARCHY_NOT_FOUND")
            try:
                current_index = hierarchy.dimension_refs.index(
                    self.drilldown.current_dimension_ref
                )
                next_index = hierarchy.dimension_refs.index(
                    self.drilldown.next_dimension_ref
                )
            except ValueError as exc:
                raise ValueError("RESEARCH_AGENT_DRILLDOWN_DIMENSION_NOT_FOUND") from exc
            if next_index != current_index + 1:
                raise ValueError("RESEARCH_AGENT_DRILLDOWN_NOT_ADJACENT")
            if self.drilldown.next_dimension_ref not in set(self.dimensions):
                raise ValueError("RESEARCH_AGENT_DRILLDOWN_NEXT_DIMENSION_REQUIRED")
            source_evidence = evidence_by_id.get(self.drilldown.source_evidence_id)
            if source_evidence is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if source_evidence.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        for value_ref in self.evidence_value_filters:
            if value_ref.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
            if value_ref.target_ref not in set(scope.allowed_filter_refs):
                raise ValueError("RESEARCH_AGENT_QUERY_FILTER_OUT_OF_SCOPE")
            evidence_item = evidence_by_id.get(value_ref.evidence_id)
            if evidence_item is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            value_ref.validate_against(evidence_item)
        for order_item in self.order:
            if order_item.ref not in set(self.metrics) | set(self.dimensions):
                raise ValueError("RESEARCH_AGENT_ORDER_REF_NOT_IN_QUERY")


class ResearchToolCall(_VersionedContractModel):
    """工具调用持久化事实，不承载公共自由参数或 Action 联合类型。"""

    run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    iteration: int = Field(ge=0)
    request_fingerprint: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_call(self) -> ResearchToolCall:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _id(self.tool_call_id, "RESEARCH_AGENT_TOOL_CALL_ID_INVALID")
        return self


class ResearchComputeRequest(_VersionedContractModel):
    """compute_evidence 的独立参数，不接受任意表达式或自由 options。"""

    run_id: str = Field(min_length=1, max_length=128)
    operation: ResearchComputeOperation
    input_evidence_ids: tuple[str, ...] = Field(min_length=1)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    group_by_refs: tuple[str, ...] = ()
    order: tuple[ResearchOrder, ...] = ()
    limit: int | None = Field(default=None, gt=0, le=1000)
    tolerance: float | None = Field(default=None, ge=0)
    purpose: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_compute_request(self) -> ResearchComputeRequest:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _unique(
            self.input_evidence_ids,
            "RESEARCH_AGENT_COMPUTE_EVIDENCE_DUPLICATED",
        )
        for evidence_id in self.input_evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        for ref in (*self.metric_refs, *self.dimension_refs):
            _ref(ref, "RESEARCH_AGENT_COMPUTE_LOGICAL_REF_INVALID")
        _unique(self.metric_refs, "RESEARCH_AGENT_COMPUTE_METRIC_DUPLICATED")
        _unique(self.dimension_refs, "RESEARCH_AGENT_COMPUTE_DIMENSION_DUPLICATED")
        _unique(self.group_by_refs, "RESEARCH_AGENT_COMPUTE_GROUP_BY_DUPLICATED")
        for ref in self.group_by_refs:
            _ref(ref, "RESEARCH_AGENT_COMPUTE_GROUP_BY_REF_INVALID")
            if not ref.startswith("DIMENSION:"):
                raise ValueError("RESEARCH_AGENT_COMPUTE_GROUP_BY_REF_INVALID")
        query_refs = set(self.metric_refs) | set(self.dimension_refs) | set(
            self.group_by_refs
        )
        if any(item.ref not in query_refs for item in self.order):
            raise ValueError("RESEARCH_AGENT_COMPUTE_ORDER_REF_INVALID")
        if self.operation is ResearchComputeOperation.RANKING and not self.order:
            raise ValueError("RESEARCH_AGENT_COMPUTE_RANK_ORDER_REQUIRED")
        if self.operation is ResearchComputeOperation.TOPN_OTHER and self.limit is None:
            raise ValueError("RESEARCH_AGENT_COMPUTE_TOP_N_LIMIT_REQUIRED")
        return self


class ResearchInspectEvidenceRequest(_VersionedContractModel):
    """inspect_evidence 的独立参数，只读取受控摘要和采样行。"""

    run_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=128)
    logical_column_refs: tuple[str, ...] = ()
    order: tuple[ResearchOrder, ...] = ()
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int | None = Field(default=None, gt=0, le=1000)
    max_rows: int = Field(default=20, gt=0, le=100)
    max_chars: int = Field(default=12_000, gt=0, le=100_000)

    @model_validator(mode="after")
    def validate_inspect_request(self) -> ResearchInspectEvidenceRequest:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _id(self.evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _unique(
            self.logical_column_refs,
            "RESEARCH_AGENT_INSPECT_COLUMN_DUPLICATED",
        )
        for ref in self.logical_column_refs:
            _ref(ref, "RESEARCH_AGENT_INSPECT_COLUMN_REF_INVALID")
        for item in self.order:
            _ref(item.ref, "RESEARCH_AGENT_INSPECT_ORDER_REF_INVALID")
        return self


class ResearchBudgetUsage(_ContractModel):
    queries: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    evidence_rows: int = Field(default=0, ge=0)
    evidence_chars: int = Field(default=0, ge=0)


class ToolObservation(_VersionedContractModel):
    """所有工具成功和失败的统一事实结果。"""

    run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    status: ToolObservationStatus
    failure_stage: ToolFailureStage | None = None
    error_code: ToolErrorCode | None = None
    error_category: str | None = Field(default=None, max_length=128)
    retryable: bool = False
    parameter_retryable: bool = False
    same_parameter_retryable: bool = False
    capability_gap: bool = False
    sql_escalation_allowed: bool = False
    message: str | None = Field(default=None, max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    semantic_plan_id: str | None = Field(default=None, max_length=128)
    result_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    statistics: dict[str, Any] = Field(default_factory=dict)
    sample_rows: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    suggested_corrections: tuple[str, ...] = ()
    budget_consumed: ResearchBudgetUsage = Field(default_factory=ResearchBudgetUsage)

    @model_validator(mode="after")
    def validate_observation(self) -> ToolObservation:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _id(self.tool_call_id, "RESEARCH_AGENT_TOOL_CALL_ID_INVALID")
        _unique(self.result_ids, "RESEARCH_AGENT_OBSERVATION_RESULT_DUPLICATED")
        _unique(self.evidence_ids, "RESEARCH_AGENT_OBSERVATION_EVIDENCE_DUPLICATED")
        for value in (*self.result_ids, *self.evidence_ids):
            _id(value, "RESEARCH_AGENT_OBSERVATION_REF_INVALID")
        _reject_physical_payload(self.details)
        _reject_physical_payload(self.statistics)
        _reject_physical_payload(self.sample_rows)
        failed = self.status is ToolObservationStatus.FAILED
        if failed:
            if self.error_code is None:
                raise ValueError("RESEARCH_AGENT_FAILURE_ERROR_CODE_REQUIRED")
            if self.failure_stage is None:
                raise ValueError("RESEARCH_AGENT_FAILURE_STAGE_REQUIRED")
            if not self.error_category:
                raise ValueError("RESEARCH_AGENT_FAILURE_CATEGORY_REQUIRED")
            if not self.message:
                raise ValueError("RESEARCH_AGENT_FAILURE_MESSAGE_REQUIRED")
            if not self.details:
                raise ValueError("RESEARCH_AGENT_FAILURE_DETAILS_REQUIRED")
        elif (
            self.failure_stage is not None
            or self.error_code is not None
            or self.error_category is not None
            or self.retryable
            or self.parameter_retryable
            or self.same_parameter_retryable
            or self.capability_gap
            or self.sql_escalation_allowed
        ):
            raise ValueError("RESEARCH_AGENT_SUCCESS_OBSERVATION_ERROR_FORBIDDEN")
        if (
            self.capability_gap
            and self.error_code is not ToolErrorCode.UNSUPPORTED_CAPABILITY
        ):
            raise ValueError("RESEARCH_AGENT_CAPABILITY_GAP_INVALID")
        if (
            self.sql_escalation_allowed
            and self.error_code is not ToolErrorCode.UNSUPPORTED_CAPABILITY
        ):
            raise ValueError("RESEARCH_AGENT_SQL_ESCALATION_INVALID")
        return self

    @property
    def stage(self) -> ToolFailureStage | None:
        """兼容工具层对 stage 的称呼；序列化字段固定为 failure_stage。"""

        return self.failure_stage


class ResearchToolCallRef(_ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=128)


class ResearchResultRef(_ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    result_id: str = Field(min_length=1, max_length=128)


class ResearchLogicalColumn(_ContractModel):
    asset_ref: str = Field(min_length=1)
    value_role: Literal[
        "group_key",
        "value",
        "current",
        "previous",
        "difference",
        "growth_rate",
        "share",
        "contribution",
    ]
    # 由 Builder 冻结逻辑资产到结果字段的映射，读取 Evidence 时禁止按位置猜测。
    result_field: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_column(self) -> ResearchLogicalColumn:
        _ref(self.asset_ref, "RESEARCH_AGENT_EVIDENCE_COLUMN_REF_INVALID")
        return self


class ResearchEvidenceStatistics(_ContractModel):
    row_count: int = Field(ge=0)
    null_count: int | None = Field(default=None, ge=0)
    positive_count: int | None = Field(default=None, ge=0)
    negative_count: int | None = Field(default=None, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> ResearchEvidenceStatistics:
        for count in (self.null_count, self.positive_count, self.negative_count):
            if count is not None and count > self.row_count:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_COUNT_EXCEEDS_ROWS")
        return self


class ResearchEvidenceDependency(_ContractModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    source_iteration: int = Field(ge=0)
    relation: str = Field(min_length=1, max_length=128)


class ResearchEvidence(_VersionedContractModel):
    """当前 Run 的受控证据摘要和结果引用，不保存完整结果。"""

    run_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=128)
    source_tool_call: ResearchToolCallRef
    result_ref: ResearchResultRef
    iteration: int = Field(ge=0)
    purpose: str = Field(min_length=1, max_length=1000)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    time_ranges: tuple[ResearchTimeRole, ...] = ()
    operations: tuple[SemanticOperation, ...] = ()
    filters: tuple[ResearchLiteralFilter, ...] = ()
    logical_columns: tuple[ResearchLogicalColumn, ...] = Field(min_length=1)
    statistics: ResearchEvidenceStatistics
    sample_rows: tuple[dict[str, Any], ...] = ()
    dependencies: tuple[ResearchEvidenceDependency, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    evidence_level: ResearchEvidenceLevel = ResearchEvidenceLevel.GOVERNED
    version_snapshot: ResearchVersionSnapshot
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> ResearchEvidence:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _id(self.evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.source_tool_call.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_TOOL_CALL_CROSS_RUN")
        if self.result_ref.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_RESULT_CROSS_RUN")
        for ref in (*self.metric_refs, *self.dimension_refs):
            _ref(ref, "RESEARCH_AGENT_EVIDENCE_LOGICAL_REF_INVALID")
        _unique(self.metric_refs, "RESEARCH_AGENT_EVIDENCE_METRIC_DUPLICATED")
        _unique(self.dimension_refs, "RESEARCH_AGENT_EVIDENCE_DIMENSION_DUPLICATED")
        _unique(self.hypothesis_ids, "RESEARCH_AGENT_EVIDENCE_HYPOTHESIS_DUPLICATED")
        dependencies = [item.evidence_id for item in self.dependencies]
        _unique(dependencies, "RESEARCH_AGENT_EVIDENCE_DEPENDENCY_DUPLICATED")
        for dependency in self.dependencies:
            if dependency.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
            if dependency.evidence_id == self.evidence_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_SELF_DEPENDENCY")
            if dependency.source_iteration >= self.iteration:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_DEPENDENCY_MUST_PRECEDE")
        _reject_physical_payload(self.sample_rows)
        return self

    def validate_against(
        self,
        requirement: ResearchAgentRequirement,
        evidence: Collection[ResearchEvidence] = (),
    ) -> None:
        """把证据与冻结 Requirement 和已有证据目录绑定。"""

        if self.run_id != requirement.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        if self.version_snapshot != requirement.version_snapshot:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_VERSION_MISMATCH")
        allowed_metrics = set(requirement.scope.target_metric_refs) | set(
            requirement.scope.driver_metric_refs
        )
        if not set(self.metric_refs) <= allowed_metrics:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_METRIC_OUT_OF_SCOPE")
        if not set(self.dimension_refs) <= set(requirement.scope.dimension_refs):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_DIMENSION_OUT_OF_SCOPE")
        if any(
            item.target_ref not in set(requirement.scope.allowed_filter_refs)
            for item in self.filters
        ):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_FILTER_OUT_OF_SCOPE")
        evidence_by_id = {item.evidence_id: item for item in evidence}
        for dependency in self.dependencies:
            source = evidence_by_id.get(dependency.evidence_id)
            if source is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if source.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
            if source.iteration != dependency.source_iteration:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_DEPENDENCY_ITERATION_MISMATCH")


class ResearchEvidenceRef(_ContractModel):
    """WorkingState 中只保存的证据引用和逻辑列摘要。"""

    run_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=128)
    iteration: int = Field(ge=0)
    logical_columns: tuple[ResearchLogicalColumn, ...] = Field(min_length=1)


class ResearchEvidenceEdge(_ContractModel):
    """Evidence DAG 的显式依赖边，供审计和恢复时重建拓扑。"""

    evidence_id: str = Field(min_length=1, max_length=128)
    depends_on: str = Field(min_length=1, max_length=128)
    relation: str = Field(min_length=1, max_length=128)
    source_iteration: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_edge(self) -> ResearchEvidenceEdge:
        _id(self.evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _id(self.depends_on, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.evidence_id == self.depends_on:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_SELF_DEPENDENCY")
        return self


class ResearchRunSnapshot(_VersionedContractModel):
    """可持久化、可恢复的 Research Run 快照（阶段 4 §8.4.1）。

    快照只保存受控摘要：完整结果在 ResultStore，样本行受 Evidence 自身
    预算约束。``state["research_state"]`` 是规范事实源（含全部成功观察，
    支撑同 tool_call_id 重放）；本快照是它的投影，额外携带运行中调用、
    失败观察、依赖边和剩余预算，供审计、时间线和恢复入口使用。
    """

    run_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1000)
    status: ResearchRunStatus = ResearchRunStatus.INITIALIZING
    finish_reason: ResearchCompletionReason | None = None
    # 前提核验结果由阶段 6 回填；v1 固定为 None。
    premise_result: dict[str, Any] | None = None
    agent_run_id: int | None = Field(default=None, ge=1)
    iteration: int = Field(default=0, ge=0)
    version_snapshot: ResearchVersionSnapshot
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    budget_usage: ResearchBudgetUsage = Field(default_factory=ResearchBudgetUsage)
    budget_remaining: ResearchBudgetRemaining
    evidences: tuple[ResearchEvidence, ...] = ()
    dependency_edges: tuple[ResearchEvidenceEdge, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    hypothesis_assessments: tuple[ResearchHypothesisAssessment, ...] = ()
    completed_tool_call_ids: tuple[str, ...] = ()
    running_tool_call_ids: tuple[str, ...] = ()
    failed_observations: tuple[ToolObservation, ...] = ()
    plan_execution_state: dict[str, Any] | None = None
    report_draft: str | None = Field(default=None, max_length=100_000)
    final_report: str | None = Field(default=None, max_length=200_000)

    @model_validator(mode="after")
    def validate_snapshot(self) -> ResearchRunSnapshot:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        if self.version_snapshot.scope_fingerprint != self.scope_fingerprint:
            raise ValueError("RESEARCH_AGENT_STATE_SCOPE_FINGERPRINT_MISMATCH")
        _unique(self.completed_tool_call_ids, "RESEARCH_AGENT_STATE_TOOL_CALL_DUPLICATED")
        _unique(self.running_tool_call_ids, "RESEARCH_AGENT_STATE_TOOL_CALL_DUPLICATED")
        overlap = set(self.completed_tool_call_ids) & set(self.running_tool_call_ids)
        if overlap:
            raise ValueError("RESEARCH_AGENT_SNAPSHOT_TOOL_CALL_STATE_CONFLICT")
        evidence_ids = [item.evidence_id for item in self.evidences]
        _unique(evidence_ids, "RESEARCH_AGENT_STATE_EVIDENCE_DUPLICATED")
        known = set(evidence_ids)
        for edge in self.dependency_edges:
            if edge.evidence_id not in known or edge.depends_on not in known:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        assessments = [item.hypothesis_id for item in self.hypothesis_assessments]
        _unique(assessments, "RESEARCH_AGENT_FINISH_HYPOTHESIS_DUPLICATED")
        for assessment in self.hypothesis_assessments:
            for evidence_id in assessment.evidence_ids:
                if evidence_id not in known:
                    raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        usage_axes = (
            (self.budget.max_queries, self.budget_usage.queries),
            (self.budget.max_model_calls, self.budget_usage.model_calls),
            (self.budget.max_iterations, self.iteration),
        )
        for limit, used in usage_axes:
            if used > limit:
                raise ValueError("RESEARCH_AGENT_SNAPSHOT_BUDGET_OVERRUN")
        expected_remaining = ResearchBudgetRemaining(
            iterations=max(self.budget.max_iterations - self.iteration, 0),
            queries=max(self.budget.max_queries - self.budget_usage.queries, 0),
            model_calls=max(
                self.budget.max_model_calls - self.budget_usage.model_calls, 0
            ),
            duration_seconds=float(
                max(
                    self.budget.max_duration_seconds
                    - self.budget_usage.duration_seconds,
                    0,
                )
            ),
        )
        if self.budget_remaining != expected_remaining:
            raise ValueError("RESEARCH_AGENT_SNAPSHOT_BUDGET_REMAINING_MISMATCH")
        terminal_statuses = {
            ResearchRunStatus.SUCCEEDED,
            ResearchRunStatus.PARTIAL,
            ResearchRunStatus.NEEDS_CLARIFICATION,
            ResearchRunStatus.FAILED,
            ResearchRunStatus.CANCELLED,
        }
        is_terminal = self.status in terminal_statuses
        if is_terminal != (self.finish_reason is not None):
            raise ValueError("RESEARCH_AGENT_STATE_COMPLETION_MISMATCH")
        return self


class ResearchBudgetRemaining(_ContractModel):
    iterations: int = Field(ge=0)
    queries: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)


class ResearchWorkingState(_VersionedContractModel):
    """可恢复的受控摘要；不接受完整结果数据。"""

    run_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1000)
    status: ResearchRunStatus = ResearchRunStatus.INITIALIZING
    iteration: int = Field(default=0, ge=0)
    target_metric_refs: tuple[str, ...] = Field(min_length=1)
    time_bindings: tuple[ResearchTimeBinding, ...] = ()
    time_bindings_by_model: dict[str, tuple[ResearchTimeBinding, ...]] = Field(
        default_factory=dict
    )
    immutable_filters: tuple[ResearchImmutableFilter, ...] = ()
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    version_snapshot: ResearchVersionSnapshot
    tool_call_ids: tuple[str, ...] = ()
    evidence_refs: tuple[ResearchEvidenceRef, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    budget_remaining: ResearchBudgetRemaining
    completion: ResearchCompletion | None = None

    @model_validator(mode="after")
    def validate_state(self) -> ResearchWorkingState:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _unique(self.target_metric_refs, "RESEARCH_AGENT_STATE_TARGET_METRIC_DUPLICATED")
        for ref in self.target_metric_refs:
            _ref(ref, "RESEARCH_AGENT_STATE_TARGET_METRIC_REF_INVALID")
        if self.version_snapshot.scope_fingerprint != self.scope_fingerprint:
            raise ValueError("RESEARCH_AGENT_STATE_SCOPE_FINGERPRINT_MISMATCH")
        _unique(
            tuple(item.role for item in self.time_bindings),
            "RESEARCH_AGENT_STATE_TIME_ROLE_DUPLICATED",
        )
        state_roles = {item.role for item in self.time_bindings}
        for model_id, bindings in self.time_bindings_by_model.items():
            if not str(model_id).isdigit() or int(model_id) <= 0:
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_INVALID")
            model_roles = tuple(item.role for item in bindings)
            if len(model_roles) != len(set(model_roles)):
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_ROLE_DUPLICATED")
            if set(model_roles) != state_roles:
                raise ValueError("RESEARCH_AGENT_TIME_BINDING_MODEL_ROLE_MISMATCH")
        _unique(
            tuple(item.target_ref for item in self.immutable_filters),
            "RESEARCH_AGENT_IMMUTABLE_FILTER_DUPLICATED",
        )
        _unique(self.tool_call_ids, "RESEARCH_AGENT_STATE_TOOL_CALL_DUPLICATED")
        _unique(self.hypothesis_ids, "RESEARCH_AGENT_STATE_HYPOTHESIS_DUPLICATED")
        evidence_ids = [item.evidence_id for item in self.evidence_refs]
        _unique(evidence_ids, "RESEARCH_AGENT_STATE_EVIDENCE_DUPLICATED")
        for item in self.evidence_refs:
            if item.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        if self.completion is not None and self.completion.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_COMPLETION_CROSS_RUN")
        terminal = self.status in {
            ResearchRunStatus.SUCCEEDED,
            ResearchRunStatus.PARTIAL,
            ResearchRunStatus.NEEDS_CLARIFICATION,
            ResearchRunStatus.FAILED,
            ResearchRunStatus.CANCELLED,
        }
        if terminal != (self.completion is not None):
            raise ValueError("RESEARCH_AGENT_STATE_COMPLETION_MISMATCH")
        if self.completion is not None and self.completion.status != self.status.value:
            raise ValueError("RESEARCH_AGENT_STATE_COMPLETION_STATUS_MISMATCH")
        return self

    def evolve(self, **changes: Any) -> ResearchWorkingState:
        """更新受控状态；冻结 WHAT 字段发生变化时明确失败。"""

        frozen_fields = {
            "run_id",
            "goal",
            "target_metric_refs",
            "time_bindings",
            "time_bindings_by_model",
            "immutable_filters",
            "scope_fingerprint",
            "version_snapshot",
        }
        for field_name in frozen_fields:
            if field_name in changes and changes[field_name] != getattr(self, field_name):
                raise ValueError("RESEARCH_AGENT_FROZEN_WHAT_CHANGED")
        payload = self.model_dump()
        payload.update(changes)
        return type(self).model_validate(payload)


class ResearchClaim(_ContractModel):
    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    claim_level: ResearchClaimLevel = ResearchClaimLevel.CORRELATION_CLUE
    confidence: Literal["high", "medium", "low"] = "medium"

    @model_validator(mode="after")
    def validate_claim(self) -> ResearchClaim:
        _unique(self.evidence_ids, "RESEARCH_AGENT_CLAIM_EVIDENCE_DUPLICATED")
        for evidence_id in self.evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_CLAIM_EVIDENCE_ID_INVALID")
        return self


class ResearchReportFinding(_ContractModel):
    """报告草案中的一条数据结论；数字必须能溯源到引用证据（§10.3.3）。"""

    statement: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"] = "medium"
    # 相关性表述与因果性表述分开声明；服务端校验措辞与强度一致。
    statement_kind: Literal["causal", "correlational"] = "correlational"
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_finding(self) -> ResearchReportFinding:
        _unique(self.evidence_ids, "RESEARCH_AGENT_FINDING_EVIDENCE_DUPLICATED")
        for evidence_id in self.evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_FINDING_EVIDENCE_ID_INVALID")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("RESEARCH_AGENT_FINDING_LIMITATION_INVALID")
        return self


class ResearchGap(_ContractModel):
    """模型根据 Evidence 内容识别出的明确缺口。"""

    gap_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_gap(self) -> ResearchGap:
        _id(self.gap_id, "RESEARCH_AGENT_GAP_ID_INVALID")
        return self


class ResearchGapResolution(_ContractModel):
    """模型使用既有 Evidence 对一个规划缺口作出的结构化裁决。"""

    gap_id: str = Field(min_length=1, max_length=128)
    status: Literal["supported", "not_supported", "undetermined"]
    evidence_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_resolution(self) -> ResearchGapResolution:
        _id(self.gap_id, "RESEARCH_AGENT_GAP_ID_INVALID")
        _unique(
            self.evidence_ids,
            "RESEARCH_AGENT_GAP_RESOLUTION_EVIDENCE_DUPLICATED",
        )
        for evidence_id in self.evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.status != "undetermined" and not self.evidence_ids:
            raise ValueError("RESEARCH_AGENT_GAP_RESOLUTION_EVIDENCE_REQUIRED")
        return self


class ResearchPlanNode(_ContractModel):
    """Planner 提交的一份完整计划中的单个可执行步骤。"""

    node_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    output_type: Literal["evidence"] = "evidence"
    expected_output: str = Field(min_length=1, max_length=1000)
    gap_id: str | None = Field(default=None, min_length=1, max_length=128)
    dependency_node_ids: tuple[str, ...] = ()
    tool_name: Literal[
        "query_semantic_data",
        "inspect_evidence",
        "compute_evidence",
    ]
    arguments: dict[str, Any] = Field(
        description=(
            "必须使用目标工具的正式参数名。query_semantic_data 使用 "
            "metrics、dimensions、time_grain、time_ranges、filters、comparison、"
            "analysis、drilldown、order、limit、purpose、hypothesis_ids。order 的每项 "
            "使用 ref=指标或维度引用、direction=asc|desc、value_role=value|current|"
            "previous|difference|growth_rate|share|contribution；按计算差值排序时 "
            "使用指标 ref 加 value_role=difference，不要把 difference 写进 ref。"
            "analysis=contribution 仅用于 comparison=contribution；日环比差异按维度"
            "定位来源使用 comparison=difference 和 analysis=breakdown；"
            "禁止使用 metric、group_by、order_by、time_filter、dataset_ref 等旧名。"
        )
    )

    @model_validator(mode="after")
    def validate_plan_node(self) -> ResearchPlanNode:
        _id(self.node_id, "RESEARCH_AGENT_PLAN_NODE_ID_INVALID")
        if self.gap_id is not None:
            _id(self.gap_id, "RESEARCH_AGENT_GAP_ID_INVALID")
        _unique(
            self.dependency_node_ids,
            "RESEARCH_AGENT_PLAN_ADDITION_DEPENDENCY_DUPLICATED",
        )
        for node_id in self.dependency_node_ids:
            _id(node_id, "RESEARCH_AGENT_PLAN_NODE_ID_INVALID")
        if self.node_id in self.dependency_node_ids:
            raise ValueError("RESEARCH_AGENT_PLAN_NODE_SELF_DEPENDENCY")
        if not self.arguments:
            raise ValueError("RESEARCH_AGENT_PLAN_NODE_ARGUMENTS_REQUIRED")
        _reject_physical_payload(self.arguments)
        return self


class SemanticAssessment(_ContractModel):
    """模型对当前 Evidence 内容充分性的结构化判断，不承载计划节点。"""

    status: SemanticAssessmentStatus
    supported_findings: tuple[ResearchReportFinding, ...] = ()
    resolved_gaps: tuple[ResearchGapResolution, ...] = ()
    unresolved_gaps: tuple[ResearchGap, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_semantic_assessment(self) -> SemanticAssessment:
        gap_ids = tuple(item.gap_id for item in self.unresolved_gaps)
        _unique(gap_ids, "RESEARCH_AGENT_GAP_DUPLICATED")
        resolved_gap_ids = tuple(item.gap_id for item in self.resolved_gaps)
        _unique(resolved_gap_ids, "RESEARCH_AGENT_RESOLVED_GAP_DUPLICATED")
        if set(gap_ids) & set(resolved_gap_ids):
            raise ValueError("RESEARCH_AGENT_GAP_STATE_CONFLICT")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("RESEARCH_AGENT_ASSESSMENT_LIMITATION_INVALID")

        if self.status is SemanticAssessmentStatus.ANSWERABLE:
            if self.unresolved_gaps:
                raise ValueError("RESEARCH_AGENT_ANSWERABLE_GAP_FORBIDDEN")
        elif self.status is SemanticAssessmentStatus.EXPLICIT_GAP:
            if not self.unresolved_gaps:
                raise ValueError("RESEARCH_AGENT_EXPLICIT_GAP_REQUIRED")
        else:
            if not self.unresolved_gaps:
                raise ValueError("RESEARCH_AGENT_TERMINAL_GAP_REQUIRED")
            if not self.limitations:
                raise ValueError("RESEARCH_AGENT_TERMINAL_LIMITATION_REQUIRED")
        return self


class ResearchHypothesisAssessment(_ContractModel):
    """finish_research 返回的最小假设评估，不判断结论强度。"""

    hypothesis_id: str = Field(min_length=1, max_length=128)
    assessment: Literal["supported", "weakened", "inconclusive", "invalid"]
    evidence_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_assessment(self) -> ResearchHypothesisAssessment:
        _id(self.hypothesis_id, "RESEARCH_AGENT_HYPOTHESIS_ID_INVALID")
        _unique(
            self.evidence_ids,
            "RESEARCH_AGENT_HYPOTHESIS_EVIDENCE_DUPLICATED",
        )
        for evidence_id in self.evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        return self


class ResearchFinishRequest(_VersionedContractModel):
    """finish_research 的独立参数；结束前由服务端再次校验证据存在性。"""

    run_id: str = Field(min_length=1, max_length=128)
    reason: ResearchCompletionReason
    semantic_assessment: SemanticAssessment
    summary: str = Field(min_length=1, max_length=4000)
    claims: tuple[ResearchClaim, ...] = ()
    findings: tuple[ResearchReportFinding, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    hypothesis_assessments: tuple[ResearchHypothesisAssessment, ...] = ()
    limitations: tuple[str, ...] = ()
    unanswered_questions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_finish_request(self) -> ResearchFinishRequest:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        _unique(self.evidence_ids, "RESEARCH_AGENT_FINISH_EVIDENCE_DUPLICATED")
        for evidence_id in self.evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        claim_ids = {
            evidence_id
            for claim in self.claims
            for evidence_id in claim.evidence_ids
        }
        if not claim_ids <= set(self.evidence_ids):
            raise ValueError("RESEARCH_AGENT_FINISH_CLAIM_CITATION_MISSING")
        finding_ids = {
            evidence_id
            for finding in self.findings
            for evidence_id in finding.evidence_ids
        }
        if not finding_ids <= set(self.evidence_ids):
            raise ValueError("RESEARCH_AGENT_FINISH_FINDING_CITATION_MISSING")
        hypothesis_ids = [item.hypothesis_id for item in self.hypothesis_assessments]
        _unique(
            hypothesis_ids,
            "RESEARCH_AGENT_FINISH_HYPOTHESIS_DUPLICATED",
        )
        assessment_evidence_ids = {
            evidence_id
            for assessment in self.hypothesis_assessments
            for evidence_id in assessment.evidence_ids
        }
        if not assessment_evidence_ids <= set(self.evidence_ids):
            raise ValueError("RESEARCH_AGENT_FINISH_HYPOTHESIS_CITATION_MISSING")
        gap_resolution_evidence_ids = {
            evidence_id
            for resolution in self.semantic_assessment.resolved_gaps
            for evidence_id in resolution.evidence_ids
        }
        if not gap_resolution_evidence_ids <= set(self.evidence_ids):
            raise ValueError("RESEARCH_AGENT_FINISH_GAP_CITATION_MISSING")
        if any(not question.strip() for question in self.unanswered_questions):
            raise ValueError("RESEARCH_AGENT_FINISH_QUESTION_INVALID")
        expected_status = {
            ResearchCompletionReason.SUFFICIENT_EVIDENCE: SemanticAssessmentStatus.ANSWERABLE,
            ResearchCompletionReason.PREMISE_NOT_SUPPORTED: SemanticAssessmentStatus.ANSWERABLE,
            ResearchCompletionReason.NO_NEW_DIRECTION: SemanticAssessmentStatus.NO_NEW_DIRECTION,
            ResearchCompletionReason.DATA_INSUFFICIENT: SemanticAssessmentStatus.DATA_INSUFFICIENT,
        }.get(self.reason)
        if (
            expected_status is not None
            and self.semantic_assessment.status is not expected_status
        ):
            raise ValueError("RESEARCH_AGENT_FINISH_ASSESSMENT_STATUS_INVALID")
        if self.semantic_assessment.status is SemanticAssessmentStatus.EXPLICIT_GAP:
            raise ValueError("RESEARCH_AGENT_EXPLICIT_GAP_CANNOT_FINISH")
        if self.findings != self.semantic_assessment.supported_findings:
            raise ValueError("RESEARCH_AGENT_FINISH_FINDINGS_ASSESSMENT_MISMATCH")
        return self


class ResearchCompletion(_VersionedContractModel):
    """结束请求；所有数据结论都必须带 Evidence 引用。"""

    run_id: str = Field(min_length=1, max_length=128)
    status: ResearchCompletionStatus
    reason: ResearchCompletionReason
    summary: str = Field(min_length=1, max_length=4000)
    claims: tuple[ResearchClaim, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_completion(self) -> ResearchCompletion:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        if RESEARCH_COMPLETION_STATUS_BY_REASON[self.reason] != self.status:
            raise ValueError("RESEARCH_AGENT_COMPLETION_REASON_INVALID")
        if self.reason in {
            ResearchCompletionReason.SUFFICIENT_EVIDENCE,
            ResearchCompletionReason.PREMISE_NOT_SUPPORTED,
            ResearchCompletionReason.NO_NEW_DIRECTION,
        } and not self.evidence_ids:
            raise ValueError("RESEARCH_AGENT_COMPLETION_EVIDENCE_REQUIRED")
        cited = set(self.evidence_ids)
        claim_ids = {evidence_id for claim in self.claims for evidence_id in claim.evidence_ids}
        if not claim_ids <= cited:
            raise ValueError("RESEARCH_AGENT_COMPLETION_CLAIM_CITATION_MISSING")
        _unique(self.evidence_ids, "RESEARCH_AGENT_COMPLETION_EVIDENCE_DUPLICATED")
        return self

    def validate_evidence(self, evidence: Collection[ResearchEvidence]) -> None:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        for evidence_id in self.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if item.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")


class ResearchEvidenceCitation(_ContractModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=1000)


class ResearchAgentReport(_VersionedContractModel):
    """可审计的 Agent 报告；Finding 与引用必须一一对应。"""

    run_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=4000)
    completion: ResearchCompletion
    findings: tuple[ResearchClaim, ...] = ()
    citations: tuple[ResearchEvidenceCitation, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_report(self) -> ResearchAgentReport:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        if self.completion.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_COMPLETION_CROSS_RUN")
        citation_id_values = [item.evidence_id for item in self.citations]
        _unique(citation_id_values, "RESEARCH_AGENT_REPORT_CITATION_DUPLICATED")
        citation_ids = {item.evidence_id for item in self.citations}
        if any(item.run_id != self.run_id for item in self.citations):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        required_ids = {
            evidence_id
            for finding in self.findings
            for evidence_id in finding.evidence_ids
        }
        if not required_ids <= citation_ids:
            raise ValueError("RESEARCH_AGENT_REPORT_FINDING_CITATION_MISSING")
        if not set(self.completion.evidence_ids) <= citation_ids:
            raise ValueError("RESEARCH_AGENT_REPORT_COMPLETION_CITATION_MISSING")
        return self

    def validate_evidence(self, evidence: Collection[ResearchEvidence]) -> None:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        for citation in self.citations:
            item = evidence_by_id.get(citation.evidence_id)
            if item is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            if item.run_id != self.run_id or citation.run_id != item.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")


class ResearchSemanticQueryOutcome(_VersionedContractModel):
    """Semantic Query Runtime 的统一成功或失败结果。"""

    run_id: str = Field(min_length=1, max_length=128)
    status: Literal["succeeded", "failed", "partial", "cancelled"]
    plan_id: str = Field(min_length=1, max_length=128)
    result_refs: tuple[ResearchResultRef, ...] = ()
    evidence: tuple[ResearchEvidence, ...] = ()
    primary_result_id: str | None = Field(default=None, min_length=1, max_length=256)
    error_code: ToolErrorCode | None = None
    failure_stage: ToolFailureStage | None = None
    retryable: bool = False
    parameter_retryable: bool = False
    same_parameter_retryable: bool = False
    capability_gap: bool = False
    sql_escalation_allowed: bool = False
    message: str | None = Field(default=None, max_length=2000)
    # 边界门失配的内部码（如 SEMANTIC_SCOPE_REQUIRED）。error_code 已映射成
    # 粗粒度 PERMISSION_DENIED 时，没有它现场无法定位是哪道门在拦（run 1306
    # 演练教训）；可选字段，旧载荷不受影响。
    internal_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_outcome(self) -> ResearchSemanticQueryOutcome:
        _id(self.run_id, "RESEARCH_AGENT_RUN_ID_INVALID")
        if self.status == "succeeded" and not self.result_refs:
            raise ValueError("RESEARCH_AGENT_QUERY_RESULT_REQUIRED")
        if self.status in {"failed", "partial", "cancelled"}:
            if self.error_code is None:
                raise ValueError("RESEARCH_AGENT_QUERY_ERROR_REQUIRED")
            if self.failure_stage is None:
                raise ValueError("RESEARCH_AGENT_QUERY_FAILURE_STAGE_REQUIRED")
        elif (
            self.error_code is not None
            or self.failure_stage is not None
            or self.retryable
            or self.parameter_retryable
            or self.same_parameter_retryable
            or self.capability_gap
            or self.sql_escalation_allowed
            or self.message is not None
        ):
            raise ValueError("RESEARCH_AGENT_QUERY_SUCCESS_ERROR_FORBIDDEN")
        for result_ref in self.result_refs:
            if result_ref.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_RESULT_CROSS_RUN")
        for evidence_item in self.evidence:
            if evidence_item.run_id != self.run_id:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        if (
            self.error_code is not ToolErrorCode.UNSUPPORTED_CAPABILITY
            and self.sql_escalation_allowed
        ):
            raise ValueError("RESEARCH_AGENT_SQL_ESCALATION_INVALID")
        if (
            self.capability_gap
            and self.error_code is not ToolErrorCode.UNSUPPORTED_CAPABILITY
        ):
            raise ValueError("RESEARCH_AGENT_CAPABILITY_GAP_INVALID")
        return self


def validate_research_semantic_query(
    query: ResearchSemanticQuery,
    requirement: ResearchAgentRequirement,
    evidence: Collection[ResearchEvidence] = (),
) -> None:
    """供 Runtime 使用的统一查询边界入口。"""

    requirement.validate_query(query, evidence)


# 阶段 1文档中的别名，保持命名向后兼容但不引入旧 Action。
ResearchToolObservation = ToolObservation
ResearchEvidenceRecord = ResearchEvidence


__all__ = [
    "RESEARCH_AGENT_CONTRACT_VERSION",
    "ResearchAgentReport",
    "ResearchAgentRequirement",
    "ResearchBudget",
    "ResearchBudgetRemaining",
    "ResearchBudgetUsage",
    "ResearchClaim",
    "ResearchClaimLevel",
    "ResearchCompletion",
    "ResearchCompletionReason",
    "ResearchCompletionStatus",
    "RESEARCH_COMPLETION_STATUS_BY_REASON",
    "ResearchComputeOperation",
    "ResearchComputeRequest",
    "ResearchDirection",
    "ResearchDriverRelationship",
    "ResearchDrilldownSpec",
    "ResearchEvidence",
    "ResearchEvidenceCitation",
    "ResearchEvidenceDependency",
    "ResearchEvidenceEdge",
    "ResearchEvidenceLevel",
    "ResearchEvidenceRecord",
    "ResearchEvidenceRef",
    "ResearchEvidenceRequirement",
    "ResearchEvidenceStatistics",
    "ResearchEvidenceValueRef",
    "ResearchGap",
    "ResearchGapResolution",
    "ResearchImmutableFilter",
    "ResearchFinishRequest",
    "ResearchHierarchy",
    "ResearchHypothesisAssessment",
    "ResearchInspectEvidenceRequest",
    "ResearchLiteralFilter",
    "ResearchLogicalColumn",
    "ResearchOrder",
    "ResearchOrderDirection",
    "ResearchPlanNode",
    "ResearchPremise",
    "ResearchPremiseType",
    "ResearchQueryComparison",
    "ResearchReason",
    "ResearchResultRef",
    "ResearchRowSelector",
    "ResearchRunSnapshot",
    "ResearchRunStatus",
    "ResearchScope",
    "ResearchSemanticQuery",
    "ResearchSemanticQueryOutcome",
    "ResearchTimeBinding",
    "ResearchTimeRole",
    "ResearchToolCall",
    "ResearchToolCallRef",
    "ResearchToolObservation",
    "ResearchVersionSnapshot",
    "ResearchWorkingState",
    "SemanticAssessment",
    "SemanticAssessmentStatus",
    "StructuralCoverage",
    "StructuralCoverageGap",
    "ToolErrorCode",
    "ToolFailureStage",
    "ToolObservation",
    "ToolObservationStatus",
    "validate_plan_required_operations",
    "validate_research_semantic_query",
]


# ---------------------------------------------------------------------------
# 41/42 目标契约
# ---------------------------------------------------------------------------

RESEARCH_AGENT_INPUT_SCHEMA_VERSION: Literal[1] = 1
RESEARCH_STATE_SCHEMA_VERSION: Literal[1] = 1
RESEARCH_STATE_SNAPSHOT_SCHEMA_VERSION: Literal[1] = 1


class _ReactSchemaModel(_ContractModel):
    """ReAct 跨边界契约的统一版本校验。"""

    SCHEMA_VERSION: ClassVar[int] = 1
    schema_version: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_schema_version(self) -> Any:
        if self.schema_version != type(self).SCHEMA_VERSION:
            raise ValueError("RESEARCH_AGENT_SCHEMA_VERSION_UNSUPPORTED")
        return self


ScalarValue = str | int | float | bool | None


def _require_ref_kind(value: str, kind: str, code: str) -> None:
    """校验语义引用格式和资产类型。"""

    _ref(value, code)
    if not value.startswith(f"{kind}:"):
        raise ValueError(code)


def _validate_ref_tuple(values: Collection[str], kind: str, code: str) -> None:
    """校验引用数组去重，并确保每项属于指定资产类型。"""

    _unique(values, f"{code}_DUPLICATED")
    for value in values:
        _require_ref_kind(value, kind, code)


class ConversationMessage(_ContractModel):
    """进入 Research Agent 的必要对话消息。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class SemanticMetric(_ContractModel):
    ref: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2_000)
    aggregation: str = Field(min_length=1, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    dimensions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_metric(self) -> SemanticMetric:
        _require_ref_kind(self.ref, "METRIC", "RESEARCH_AGENT_SEMANTIC_METRIC_REF_INVALID")
        _validate_ref_tuple(
            self.dimensions,
            "DIMENSION",
            "RESEARCH_AGENT_SEMANTIC_METRIC_DIMENSION_REF_INVALID",
        )
        return self


class SemanticDimension(_ContractModel):
    ref: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2_000)
    grains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_dimension(self) -> SemanticDimension:
        _require_ref_kind(
            self.ref,
            "DIMENSION",
            "RESEARCH_AGENT_SEMANTIC_DIMENSION_REF_INVALID",
        )
        _unique(self.grains, "RESEARCH_AGENT_SEMANTIC_DIMENSION_GRAIN_DUPLICATED")
        return self


class SemanticHierarchy(_ContractModel):
    ref: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=256)
    levels: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> SemanticHierarchy:
        _require_ref_kind(
            self.ref,
            "HIERARCHY",
            "RESEARCH_AGENT_SEMANTIC_HIERARCHY_REF_INVALID",
        )
        _validate_ref_tuple(
            self.levels,
            "DIMENSION",
            "RESEARCH_AGENT_SEMANTIC_HIERARCHY_LEVEL_REF_INVALID",
        )
        return self


class MetricFormula(_ContractModel):
    target_metric_ref: str = Field(min_length=1)
    expression: str = Field(min_length=1, max_length=2_000)
    source_metric_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_formula(self) -> MetricFormula:
        _require_ref_kind(
            self.target_metric_ref,
            "METRIC",
            "RESEARCH_AGENT_METRIC_FORMULA_TARGET_REF_INVALID",
        )
        _validate_ref_tuple(
            self.source_metric_refs,
            "METRIC",
            "RESEARCH_AGENT_METRIC_FORMULA_SOURCE_REF_INVALID",
        )
        return self


class MetricAnalysisRelation(_ContractModel):
    metric_ref: str = Field(min_length=1)
    related_metric_refs: tuple[str, ...] = Field(min_length=1)
    analysis_type: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_analysis_relation(self) -> MetricAnalysisRelation:
        _require_ref_kind(
            self.metric_ref,
            "METRIC",
            "RESEARCH_AGENT_ANALYSIS_METRIC_REF_INVALID",
        )
        _validate_ref_tuple(
            self.related_metric_refs,
            "METRIC",
            "RESEARCH_AGENT_ANALYSIS_RELATED_METRIC_REF_INVALID",
        )
        return self


class SemanticAmbiguity(_ContractModel):
    term: str = Field(min_length=1, max_length=256)
    candidate_refs: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_ambiguity(self) -> SemanticAmbiguity:
        _unique(self.candidate_refs, "RESEARCH_AGENT_AMBIGUITY_CANDIDATE_DUPLICATED")
        for value in self.candidate_refs:
            _ref(value, "RESEARCH_AGENT_AMBIGUITY_CANDIDATE_REF_INVALID")
        return self


class SemanticContext(_ContractModel):
    """权限过滤和预算裁剪后的语义资产目录。"""

    metrics: tuple[SemanticMetric, ...] = ()
    dimensions: tuple[SemanticDimension, ...] = ()
    hierarchies: tuple[SemanticHierarchy, ...] = ()
    metric_formulas: tuple[MetricFormula, ...] = ()
    metric_analysis_relations: tuple[MetricAnalysisRelation, ...] = ()
    ambiguities: tuple[SemanticAmbiguity, ...] = ()

    @model_validator(mode="after")
    def validate_context(self) -> SemanticContext:
        for name, refs in (
            ("metrics", tuple(item.ref for item in self.metrics)),
            ("dimensions", tuple(item.ref for item in self.dimensions)),
            ("hierarchies", tuple(item.ref for item in self.hierarchies)),
            (
                "metric_formulas",
                tuple(item.target_metric_ref for item in self.metric_formulas),
            ),
            (
                "metric_analysis_relations",
                tuple(item.metric_ref for item in self.metric_analysis_relations),
            ),
            ("ambiguities", tuple(item.term for item in self.ambiguities)),
        ):
            _unique(refs, f"RESEARCH_AGENT_SEMANTIC_CONTEXT_{name.upper()}_DUPLICATED")
        return self


class ResearchAgentInput(_ReactSchemaModel):
    """Research Run 使用的不可修改输入快照。"""

    SCHEMA_VERSION: ClassVar[int] = RESEARCH_AGENT_INPUT_SCHEMA_VERSION
    agent_input_ref: str | None = Field(default=None, max_length=256)
    user_question: str = Field(min_length=1, max_length=20_000)
    conversation_context: tuple[ConversationMessage, ...] = ()
    semantic_context: SemanticContext

    @model_validator(mode="after")
    def validate_input(self) -> ResearchAgentInput:
        if self.agent_input_ref is not None:
            _id(self.agent_input_ref, "RESEARCH_AGENT_INPUT_REF_INVALID")
        return self


class RemainingBudget(_ContractModel):
    """每轮投影给模型的剩余预算。"""

    model_turns: int = Field(ge=0)
    query_calls: int = Field(ge=0)
    compute_calls: int = Field(ge=0)
    semantic_search_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
    query_cost: float = Field(ge=0)


class BudgetUsage(_ContractModel):
    """Research Run 的累计资源使用量。"""

    model_turns: int = Field(default=0, ge=0)
    query_calls: int = Field(default=0, ge=0)
    compute_calls: int = Field(default=0, ge=0)
    semantic_search_calls: int = Field(default=0, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)
    query_cost: float = Field(default=0, ge=0)


class TimeRange(_ContractModel):
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)
    granularity: str = Field(min_length=1, max_length=32)


class EvidenceFilter(_ContractModel):
    field_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: ScalarValue

    @model_validator(mode="after")
    def validate_evidence_filter(self) -> EvidenceFilter:
        _ref(self.field_ref, "RESEARCH_AGENT_EVIDENCE_FILTER_REF_INVALID")
        _reject_physical_payload(self.value)
        return self


class EvidenceComparison(_ContractModel):
    base_period: str = Field(min_length=1, max_length=64)
    against_period: str = Field(min_length=1, max_length=64)
    outputs: tuple[
        Literal["current", "previous", "difference", "growth_rate"], ...
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_outputs(self) -> EvidenceComparison:
        _unique(self.outputs, "RESEARCH_AGENT_EVIDENCE_COMPARISON_OUTPUT_DUPLICATED")
        return self


class EvidenceComputation(_ContractModel):
    operation: str = Field(min_length=1, max_length=64)
    input_evidence_ids: tuple[str, ...] = Field(min_length=1)
    parameters: tuple[tuple[str, ScalarValue], ...] = ()

    @model_validator(mode="after")
    def validate_computation(self) -> EvidenceComputation:
        _unique(
            self.input_evidence_ids,
            "RESEARCH_AGENT_EVIDENCE_COMPUTATION_INPUT_DUPLICATED",
        )
        for evidence_id in self.input_evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _unique(
            tuple(key for key, _value in self.parameters),
            "RESEARCH_AGENT_EVIDENCE_COMPUTATION_PARAMETER_DUPLICATED",
        )
        _reject_physical_payload(self.parameters)
        return self


class EvidenceDefinition(_ContractModel):
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    time_ranges: tuple[TimeRange, ...] = ()
    filters: tuple[EvidenceFilter, ...] = ()
    comparison: EvidenceComparison | None = None
    computation: EvidenceComputation | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> EvidenceDefinition:
        _validate_ref_tuple(
            self.metrics,
            "METRIC",
            "RESEARCH_AGENT_EVIDENCE_METRIC_REF_INVALID",
        )
        _validate_ref_tuple(
            self.dimensions,
            "DIMENSION",
            "RESEARCH_AGENT_EVIDENCE_DIMENSION_REF_INVALID",
        )
        return self


class EvidenceColumn(_ContractModel):
    name: str = Field(min_length=1, max_length=256)
    semantic_ref: str | None = Field(default=None, max_length=256)
    role: Literal["dimension", "metric", "computed"]
    data_type: str = Field(min_length=1, max_length=64)
    unit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_column(self) -> EvidenceColumn:
        if self.semantic_ref is not None:
            _ref(self.semantic_ref, "RESEARCH_AGENT_EVIDENCE_COLUMN_REF_INVALID")
        return self


class EvidenceStatistic(_ContractModel):
    name: str = Field(min_length=1, max_length=128)
    value: ScalarValue
    unit: str | None = Field(default=None, max_length=64)


class EvidenceData(_ContractModel):
    row_count: int = Field(ge=0)
    truncated: bool = False
    rows: tuple[tuple[ScalarValue, ...], ...] = ()
    statistics: tuple[EvidenceStatistic, ...] = ()


class EvidenceLimitation(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    impact: str = Field(min_length=1, max_length=2_000)


class Evidence(_ReactSchemaModel):
    """查询或计算产生的、可审计的结果证据。"""

    evidence_id: str = Field(min_length=1, max_length=256)
    evidence_type: Literal["query_result", "computation_result"]
    purpose: str = Field(min_length=1, max_length=2_000)
    definition: EvidenceDefinition
    columns: tuple[EvidenceColumn, ...] = Field(min_length=1)
    data: EvidenceData
    parent_evidence_ids: tuple[str, ...] = ()
    limitations: tuple[EvidenceLimitation, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> Evidence:
        if not self.evidence_id.startswith("evidence:"):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _id(self.evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _unique(
            self.parent_evidence_ids,
            "RESEARCH_AGENT_EVIDENCE_PARENT_DUPLICATED",
        )
        for evidence_id in self.parent_evidence_ids:
            _id(evidence_id, "RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.data.row_count < len(self.data.rows):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ROW_COUNT_INVALID")
        if any(len(row) != len(self.columns) for row in self.data.rows):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ROW_WIDTH_INVALID")
        if self.evidence_type == "computation_result" and not self.parent_evidence_ids:
            raise ValueError("RESEARCH_AGENT_COMPUTATION_PARENT_EVIDENCE_REQUIRED")
        return self

    @staticmethod
    def evidence_id_for_tool_call(tool_call_id: str) -> str:
        """按 Tool Call 标识生成确定性的 Evidence 标识。"""

        _id(tool_call_id, "RESEARCH_AGENT_TOOL_CALL_ID_INVALID")
        return f"evidence:{tool_call_id}"


class EvidenceRows(_ContractModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    columns: tuple[EvidenceColumn, ...] = Field(min_length=1)
    rows: tuple[tuple[ScalarValue, ...], ...] = ()
    total_row_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_rows(self) -> EvidenceRows:
        if not self.evidence_id.startswith("evidence:"):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.total_row_count < self.offset + len(self.rows) and not self.truncated:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ROWS_RANGE_INVALID")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ROW_WIDTH_INVALID")
        return self


class ResearchExecutionErrorStage(StrEnum):
    PARSING = "parsing"
    VISIBILITY = "visibility"
    VALIDATION = "validation"
    PERMISSION = "permission"
    PLANNING = "planning"
    COMPILATION = "compilation"
    EXECUTION = "execution"
    PERSISTENCE = "persistence"
    BUDGET = "budget"
    COMPLETION = "completion"


class ExecutionError(_ReactSchemaModel):
    code: str = Field(min_length=1, max_length=128)
    stage: ResearchExecutionErrorStage
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False
    parameter_retryable: bool = False
    same_parameter_retryable: bool = False

    @model_validator(mode="after")
    def validate_retry_flags(self) -> ExecutionError:
        if self.parameter_retryable and not self.retryable:
            raise ValueError("RESEARCH_AGENT_PARAMETER_RETRY_REQUIRES_RETRYABLE")
        if self.same_parameter_retryable and not self.retryable:
            raise ValueError("RESEARCH_AGENT_SAME_PARAMETER_RETRY_REQUIRES_RETRYABLE")
        return self


class ResearchActionType(StrEnum):
    QUERY_SEMANTIC_DATA = "query_semantic_data"
    COMPUTE_EVIDENCE = "compute_evidence"
    READ_EVIDENCE_ROWS = "read_evidence_rows"
    SEARCH_SEMANTIC_ASSETS = "search_semantic_assets"
    REQUEST_CLARIFICATION = "request_clarification"
    FINISH_RESEARCH = "finish_research"


class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING_FOR_USER = "waiting_for_user"


_ResearchResultT = TypeVar("_ResearchResultT", bound=BaseModel)


class ToolResult(_ReactSchemaModel, Generic[_ResearchResultT]):
    """Research 工具统一结果信封；外层由 Runtime 构造。"""

    tool_call_id: str = Field(min_length=1, max_length=256)
    name: ResearchActionType
    status: ToolResultStatus
    result: _ResearchResultT | None = None
    error: ExecutionError | None = None

    @model_validator(mode="after")
    def validate_result_envelope(self) -> ToolResult[_ResearchResultT]:
        _id(self.tool_call_id, "RESEARCH_AGENT_TOOL_CALL_ID_INVALID")
        if self.status is ToolResultStatus.SUCCEEDED:
            if self.result is None or self.error is not None:
                raise ValueError("RESEARCH_AGENT_TOOL_RESULT_SUCCESS_CONTRACT_INVALID")
        elif self.status is ToolResultStatus.FAILED:
            if self.error is None or self.result is not None:
                raise ValueError("RESEARCH_AGENT_TOOL_RESULT_FAILURE_CONTRACT_INVALID")
        else:
            if self.name is not ResearchActionType.REQUEST_CLARIFICATION:
                raise ValueError("RESEARCH_AGENT_WAITING_STATUS_TOOL_INVALID")
            if self.result is None or self.error is not None:
                raise ValueError("RESEARCH_AGENT_TOOL_RESULT_WAITING_CONTRACT_INVALID")
        return self


class QueryPeriod(_ContractModel):
    role: str = Field(min_length=1, max_length=64)
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)


class QueryTimeSpec(_ContractModel):
    dimension_ref: str = Field(min_length=1)
    grain: Literal["day", "week", "month", "quarter", "year"]
    periods: tuple[QueryPeriod, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_spec(self) -> QueryTimeSpec:
        _require_ref_kind(
            self.dimension_ref,
            "DIMENSION",
            "RESEARCH_AGENT_QUERY_TIME_DIMENSION_REF_INVALID",
        )
        roles = tuple(item.role for item in self.periods)
        _unique(roles, "RESEARCH_AGENT_QUERY_TIME_PERIOD_ROLE_DUPLICATED")
        return self


class EvidenceSelector(_ContractModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    column_ref: str = Field(min_length=1)
    selection: Literal["all", "top", "bottom"]

    @model_validator(mode="after")
    def validate_selector(self) -> EvidenceSelector:
        if not self.evidence_id.startswith("evidence:"):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _ref(self.column_ref, "RESEARCH_AGENT_EVIDENCE_SELECTOR_COLUMN_REF_INVALID")
        return self


class QueryFilter(_ContractModel):
    field_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1, max_length=32)
    value: ScalarValue = None
    evidence_selector: EvidenceSelector | None = None

    @model_validator(mode="after")
    def validate_query_filter(self) -> QueryFilter:
        _ref(self.field_ref, "RESEARCH_AGENT_QUERY_FILTER_REF_INVALID")
        has_value = self.value is not None
        has_selector = self.evidence_selector is not None
        if has_value == has_selector:
            raise ValueError("RESEARCH_AGENT_QUERY_FILTER_VALUE_EXCLUSIVE")
        _reject_physical_payload(self.value)
        return self


class QueryComparison(_ContractModel):
    base_period: str = Field(min_length=1, max_length=64)
    against_period: str = Field(min_length=1, max_length=64)
    outputs: tuple[
        Literal["current", "previous", "difference", "growth_rate"], ...
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_comparison(self) -> QueryComparison:
        _unique(self.outputs, "RESEARCH_AGENT_QUERY_COMPARISON_OUTPUT_DUPLICATED")
        return self


class QueryOrder(_ContractModel):
    field_ref: str = Field(min_length=1)
    value_role: str = Field(min_length=1, max_length=64)
    direction: Literal["asc", "desc"]

    @model_validator(mode="after")
    def validate_order(self) -> QueryOrder:
        _ref(self.field_ref, "RESEARCH_AGENT_QUERY_ORDER_REF_INVALID")
        return self


class QueryResultSpec(_ContractModel):
    order_by: tuple[QueryOrder, ...] = ()
    limit: int = Field(gt=0, le=10_000)


class QuerySemanticDataArguments(_ReactSchemaModel):
    metrics: tuple[str, ...] = Field(min_length=1)
    dimensions: tuple[str, ...] = ()
    time: QueryTimeSpec | None = None
    filters: tuple[QueryFilter, ...] = ()
    comparison: QueryComparison | None = None
    result: QueryResultSpec

    @model_validator(mode="after")
    def validate_query_arguments(self) -> QuerySemanticDataArguments:
        _validate_ref_tuple(
            self.metrics,
            "METRIC",
            "RESEARCH_AGENT_QUERY_METRIC_REF_INVALID",
        )
        _validate_ref_tuple(
            self.dimensions,
            "DIMENSION",
            "RESEARCH_AGENT_QUERY_DIMENSION_REF_INVALID",
        )
        return self


class ComputeEvidenceArguments(_ReactSchemaModel):
    operation: Literal[
        "difference",
        "growth_rate",
        "ratio",
        "share",
        "contribution",
        "ranking",
        "topn_other",
        "merge",
        "reconciliation",
    ]
    input_evidence_ids: tuple[str, ...] = Field(min_length=1)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    group_by_refs: tuple[str, ...] = ()
    order_by: tuple[QueryOrder, ...] = ()
    limit: int | None = Field(default=None, gt=0, le=10_000)
    tolerance: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_compute_arguments(self) -> ComputeEvidenceArguments:
        _unique(self.input_evidence_ids, "RESEARCH_AGENT_COMPUTE_INPUT_DUPLICATED")
        for evidence_id in self.input_evidence_ids:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _validate_ref_tuple(
            self.metric_refs,
            "METRIC",
            "RESEARCH_AGENT_COMPUTE_METRIC_REF_INVALID",
        )
        _validate_ref_tuple(
            (*self.dimension_refs, *self.group_by_refs),
            "DIMENSION",
            "RESEARCH_AGENT_COMPUTE_DIMENSION_REF_INVALID",
        )
        return self


class ReadEvidenceRowsArguments(_ReactSchemaModel):
    evidence_id: str = Field(min_length=1, max_length=256)
    column_refs: tuple[str, ...] = Field(min_length=1)
    order_by: tuple[QueryOrder, ...] = ()
    offset: int = Field(default=0, ge=0)
    limit: int = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def validate_read_arguments(self) -> ReadEvidenceRowsArguments:
        if not self.evidence_id.startswith("evidence:"):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        for column_ref in self.column_refs:
            _ref(column_ref, "RESEARCH_AGENT_READ_COLUMN_REF_INVALID")
        _unique(self.column_refs, "RESEARCH_AGENT_READ_COLUMN_REF_DUPLICATED")
        return self


class SearchSemanticAssetsArguments(_ReactSchemaModel):
    query: str = Field(min_length=1, max_length=2_000)
    asset_types: tuple[
        Literal["metric", "dimension", "hierarchy", "metric_formula", "metric_analysis_relation"], ...
    ] = Field(min_length=1)
    related_asset_refs: tuple[str, ...] = ()
    limit: int = Field(gt=0, le=100)

    @model_validator(mode="after")
    def validate_search_arguments(self) -> SearchSemanticAssetsArguments:
        _unique(self.asset_types, "RESEARCH_AGENT_SEARCH_ASSET_TYPE_DUPLICATED")
        _unique(self.related_asset_refs, "RESEARCH_AGENT_SEARCH_RELATED_REF_DUPLICATED")
        for value in self.related_asset_refs:
            _ref(value, "RESEARCH_AGENT_SEARCH_RELATED_REF_INVALID")
        return self


class ClarificationOption(_ContractModel):
    option_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=1_000)
    semantic_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_option(self) -> ClarificationOption:
        _id(self.option_id, "RESEARCH_AGENT_CLARIFICATION_OPTION_ID_INVALID")
        _unique(self.semantic_refs, "RESEARCH_AGENT_CLARIFICATION_REF_DUPLICATED")
        for value in self.semantic_refs:
            _ref(value, "RESEARCH_AGENT_CLARIFICATION_REF_INVALID")
        return self


class RequestClarificationArguments(_ReactSchemaModel):
    question: str = Field(min_length=1, max_length=2_000)
    options: tuple[ClarificationOption, ...] = Field(min_length=1)
    allow_free_text: bool = False

    @model_validator(mode="after")
    def validate_clarification_arguments(self) -> RequestClarificationArguments:
        _unique(
            tuple(item.option_id for item in self.options),
            "RESEARCH_AGENT_CLARIFICATION_OPTION_DUPLICATED",
        )
        return self


class CompletionLimitation(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    impact: str = Field(min_length=1, max_length=2_000)
    attempt_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_limitation(self) -> CompletionLimitation:
        _unique(self.attempt_ids, "RESEARCH_AGENT_COMPLETION_ATTEMPT_DUPLICATED")
        for attempt_id in self.attempt_ids:
            _id(attempt_id, "RESEARCH_AGENT_ATTEMPT_ID_INVALID")
        return self


class Completion(_ReactSchemaModel):
    status: Literal["complete", "partial", "unanswerable"]
    summary: str = Field(min_length=1, max_length=4_000)
    finding_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[CompletionLimitation, ...] = ()

    @model_validator(mode="after")
    def validate_completion_contract(self) -> Completion:
        _unique(self.finding_ids, "RESEARCH_AGENT_COMPLETION_FINDING_DUPLICATED")
        _unique(self.evidence_ids, "RESEARCH_AGENT_COMPLETION_EVIDENCE_DUPLICATED")
        for finding_id in self.finding_ids:
            _id(finding_id, "RESEARCH_AGENT_FINDING_ID_INVALID")
        for evidence_id in self.evidence_ids:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.status == "complete" and not self.evidence_ids:
            raise ValueError("RESEARCH_AGENT_COMPLETION_EVIDENCE_REQUIRED")
        if self.status in {"partial", "unanswerable"} and not self.limitations:
            raise ValueError("RESEARCH_AGENT_COMPLETION_LIMITATION_REQUIRED")
        return self


class FinishResearchResult(_ReactSchemaModel):
    decision: Literal["accepted", "rejected"]
    message: str = Field(min_length=1, max_length=2_000)
    completion_status: Literal["complete", "partial", "unanswerable"] | None = None
    validation_errors: tuple[CompletionValidationError, ...] = ()

    @model_validator(mode="after")
    def validate_finish_result(self) -> FinishResearchResult:
        if self.decision == "accepted" and self.completion_status is None:
            raise ValueError("RESEARCH_AGENT_FINISH_STATUS_REQUIRED")
        if self.decision == "rejected" and self.completion_status is not None:
            raise ValueError("RESEARCH_AGENT_FINISH_REJECTED_STATUS_FORBIDDEN")
        return self


class CompletionValidationError(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    finding_id: str | None = Field(default=None, max_length=128)
    evidence_id: str | None = Field(default=None, max_length=256)


class FinishResearchArguments(_ReactSchemaModel):
    completion: Completion


class FindingScope(_ContractModel):
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    time_ranges: tuple[TimeRange, ...] = ()
    filters: tuple[EvidenceFilter, ...] = ()

    @model_validator(mode="after")
    def validate_finding_scope(self) -> FindingScope:
        _validate_ref_tuple(
            self.metric_refs,
            "METRIC",
            "RESEARCH_AGENT_FINDING_METRIC_REF_INVALID",
        )
        _validate_ref_tuple(
            self.dimension_refs,
            "DIMENSION",
            "RESEARCH_AGENT_FINDING_DIMENSION_REF_INVALID",
        )
        return self


class Finding(_ContractModel):
    finding_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    scope: FindingScope
    status: Literal["confirmed", "superseded"] = "confirmed"

    @model_validator(mode="after")
    def validate_finding_contract(self) -> Finding:
        _id(self.finding_id, "RESEARCH_AGENT_FINDING_ID_INVALID")
        _unique(self.evidence_ids, "RESEARCH_AGENT_FINDING_EVIDENCE_DUPLICATED")
        for evidence_id in self.evidence_ids:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        return self


class TodoItem(_ContractModel):
    todo_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=2_000)
    status: Literal["pending", "in_progress", "completed", "skipped"]
    order: int = Field(gt=0)
    related_evidence_ids: tuple[str, ...] = ()
    result_reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_todo(self) -> TodoItem:
        _id(self.todo_id, "RESEARCH_AGENT_TODO_ID_INVALID")
        _unique(self.related_evidence_ids, "RESEARCH_AGENT_TODO_EVIDENCE_DUPLICATED")
        for evidence_id in self.related_evidence_ids:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.status in {"completed", "skipped"} and not self.result_reason:
            raise ValueError("RESEARCH_AGENT_TODO_RESULT_REASON_REQUIRED")
        return self


class AttemptSummary(_ContractModel):
    attempt_id: str = Field(min_length=1, max_length=128)
    action_type: ResearchActionType
    purpose: str = Field(min_length=1, max_length=2_000)
    parameter_summary: str = Field(default="", max_length=2_000)
    action_fingerprint: str = Field(min_length=1, max_length=256)
    status: Literal["succeeded", "failed", "rejected", "waiting_for_user"]
    produced_evidence_ids: tuple[str, ...] = ()
    error: ExecutionError | None = None

    @model_validator(mode="after")
    def validate_attempt(self) -> AttemptSummary:
        _id(self.attempt_id, "RESEARCH_AGENT_ATTEMPT_ID_INVALID")
        _unique(
            self.produced_evidence_ids,
            "RESEARCH_AGENT_ATTEMPT_EVIDENCE_DUPLICATED",
        )
        for evidence_id in self.produced_evidence_ids:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        if self.status in {"failed", "rejected"} and self.error is None:
            raise ValueError("RESEARCH_AGENT_ATTEMPT_ERROR_REQUIRED")
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("RESEARCH_AGENT_ATTEMPT_SUCCESS_ERROR_FORBIDDEN")
        return self


class ResearchStateStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNANSWERABLE = "unanswerable"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchState(_ReactSchemaModel):
    """Research Run 的规范状态，不保存完整 Evidence 数据。"""

    SCHEMA_VERSION: ClassVar[int] = RESEARCH_STATE_SCHEMA_VERSION
    agent_input_ref: str = Field(min_length=1, max_length=256)
    evidence_refs: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    todo_items: tuple[TodoItem, ...] = ()
    attempted_actions: tuple[AttemptSummary, ...] = ()
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    current_status: ResearchStateStatus = ResearchStateStatus.RUNNING
    completion: Completion | None = None

    @model_validator(mode="after")
    def validate_state_contract(self) -> ResearchState:
        _id(self.agent_input_ref, "RESEARCH_AGENT_INPUT_REF_INVALID")
        _unique(self.evidence_refs, "RESEARCH_AGENT_STATE_EVIDENCE_DUPLICATED")
        for evidence_id in self.evidence_refs:
            if not evidence_id.startswith("evidence:"):
                raise ValueError("RESEARCH_AGENT_EVIDENCE_ID_INVALID")
        _unique(
            tuple(item.finding_id for item in self.findings),
            "RESEARCH_AGENT_STATE_FINDING_DUPLICATED",
        )
        _unique(
            tuple(item.todo_id for item in self.todo_items),
            "RESEARCH_AGENT_STATE_TODO_DUPLICATED",
        )
        _unique(
            tuple(item.attempt_id for item in self.attempted_actions),
            "RESEARCH_AGENT_STATE_ATTEMPT_DUPLICATED",
        )
        terminal_statuses = {
            ResearchStateStatus.COMPLETED,
            ResearchStateStatus.PARTIAL,
            ResearchStateStatus.UNANSWERABLE,
        }
        if self.current_status in terminal_statuses and self.completion is None:
            raise ValueError("RESEARCH_AGENT_STATE_COMPLETION_REQUIRED")
        if self.current_status is ResearchStateStatus.WAITING_FOR_USER and self.completion is not None:
            raise ValueError("RESEARCH_AGENT_WAITING_COMPLETION_FORBIDDEN")
        if self.completion is not None:
            expected_status = {
                "complete": ResearchStateStatus.COMPLETED,
                "partial": ResearchStateStatus.PARTIAL,
                "unanswerable": ResearchStateStatus.UNANSWERABLE,
            }[self.completion.status]
            if self.current_status is not expected_status:
                raise ValueError("RESEARCH_AGENT_STATE_COMPLETION_STATUS_INVALID")
        return self


class FindingChange(_ContractModel):
    change_type: Literal["add", "supersede"]
    finding: Finding | None = None
    finding_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_finding_change(self) -> FindingChange:
        if self.change_type == "add":
            if self.finding is None or self.finding_id is not None:
                raise ValueError("RESEARCH_AGENT_FINDING_ADD_PAYLOAD_INVALID")
        elif self.finding_id is None or self.finding is not None:
            raise ValueError("RESEARCH_AGENT_FINDING_SUPERSEDE_PAYLOAD_INVALID")
        if self.finding_id is not None:
            _id(self.finding_id, "RESEARCH_AGENT_FINDING_ID_INVALID")
        return self


class TodoChange(_ContractModel):
    change_type: Literal["add", "set_status", "set_order"]
    todo: TodoItem | None = None
    todo_id: str | None = Field(default=None, max_length=128)
    status: Literal["pending", "in_progress", "completed", "skipped"] | None = None
    order: int | None = Field(default=None, gt=0)
    result_reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_todo_change(self) -> TodoChange:
        if self.change_type == "add":
            if self.todo is None or any(
                value is not None
                for value in (self.todo_id, self.status, self.order, self.result_reason)
            ):
                raise ValueError("RESEARCH_AGENT_TODO_ADD_PAYLOAD_INVALID")
        elif self.change_type == "set_status":
            if self.todo is not None or self.todo_id is None or self.status is None or self.order is not None:
                raise ValueError("RESEARCH_AGENT_TODO_STATUS_PAYLOAD_INVALID")
            _id(self.todo_id, "RESEARCH_AGENT_TODO_ID_INVALID")
            if self.status in {"completed", "skipped"} and not self.result_reason:
                raise ValueError("RESEARCH_AGENT_TODO_RESULT_REASON_REQUIRED")
        elif self.todo is not None or self.todo_id is None or self.order is None or self.status is not None or self.result_reason is not None:
            raise ValueError("RESEARCH_AGENT_TODO_ORDER_PAYLOAD_INVALID")
        if self.todo_id is not None:
            _id(self.todo_id, "RESEARCH_AGENT_TODO_ID_INVALID")
        return self


class QuerySemanticDataAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.QUERY_SEMANTIC_DATA] = ResearchActionType.QUERY_SEMANTIC_DATA
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: str | None = Field(default=None, max_length=2_000)
    arguments: QuerySemanticDataArguments


class ComputeEvidenceAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.COMPUTE_EVIDENCE] = ResearchActionType.COMPUTE_EVIDENCE
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: str | None = Field(default=None, max_length=2_000)
    arguments: ComputeEvidenceArguments


class ReadEvidenceRowsAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.READ_EVIDENCE_ROWS] = ResearchActionType.READ_EVIDENCE_ROWS
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: str | None = Field(default=None, max_length=2_000)
    arguments: ReadEvidenceRowsArguments


class SearchSemanticAssetsAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.SEARCH_SEMANTIC_ASSETS] = ResearchActionType.SEARCH_SEMANTIC_ASSETS
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: str | None = Field(default=None, max_length=2_000)
    arguments: SearchSemanticAssetsArguments


class RequestClarificationAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.REQUEST_CLARIFICATION] = ResearchActionType.REQUEST_CLARIFICATION
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: str | None = Field(default=None, max_length=2_000)
    arguments: RequestClarificationArguments


class FinishResearchAction(_ReactSchemaModel):
    action_type: Literal[ResearchActionType.FINISH_RESEARCH] = ResearchActionType.FINISH_RESEARCH
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_result: None = None
    arguments: FinishResearchArguments


ResearchAction = Annotated[
    QuerySemanticDataAction
    | ComputeEvidenceAction
    | ReadEvidenceRowsAction
    | SearchSemanticAssetsAction
    | RequestClarificationAction
    | FinishResearchAction,
    Field(discriminator="action_type"),
]


class ResearchTurnDecision(_ReactSchemaModel):
    """模型每轮提交的 Finding/Todo 增量和工具动作。"""

    finding_changes: tuple[FindingChange, ...] = ()
    todo_changes: tuple[TodoChange, ...] = ()
    actions: tuple[ResearchAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_turn_decision(self) -> ResearchTurnDecision:
        action_types = tuple(item.action_type for item in self.actions)
        control_types = {
            ResearchActionType.REQUEST_CLARIFICATION,
            ResearchActionType.FINISH_RESEARCH,
        }
        if control_types.intersection(action_types):
            if len(self.actions) != 1:
                raise ValueError("RESEARCH_AGENT_CONTROL_ACTION_MUST_BE_ALONE")
        elif len(self.actions) > 1:
            read_only_types = {
                ResearchActionType.QUERY_SEMANTIC_DATA,
                ResearchActionType.COMPUTE_EVIDENCE,
                ResearchActionType.READ_EVIDENCE_ROWS,
            }
            if not set(action_types) <= read_only_types:
                raise ValueError("RESEARCH_AGENT_PARALLEL_ACTION_NOT_READ_ONLY")
        return self


class ResearchStateSnapshot(_ReactSchemaModel):
    """ResearchState 的持久化快照。"""

    SCHEMA_VERSION: ClassVar[int] = RESEARCH_STATE_SNAPSHOT_SCHEMA_VERSION
    runtime_type: Literal["research_agent"] = "research_agent"
    state: ResearchState
    last_event_sequence: int = Field(ge=0)
    input_snapshot_ref: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_snapshot(self) -> ResearchStateSnapshot:
        _id(self.input_snapshot_ref, "RESEARCH_AGENT_INPUT_REF_INVALID")
        if self.state.agent_input_ref != self.input_snapshot_ref:
            raise ValueError("RESEARCH_AGENT_SNAPSHOT_INPUT_REF_MISMATCH")
        return self


__all__.extend(
    [
        "AttemptSummary",
        "BudgetUsage",
        "ClarificationOption",
        "Completion",
        "CompletionLimitation",
        "CompletionValidationError",
        "ComputeEvidenceAction",
        "ComputeEvidenceArguments",
        "ConversationMessage",
        "Evidence",
        "EvidenceColumn",
        "EvidenceComparison",
        "EvidenceComputation",
        "EvidenceData",
        "EvidenceDefinition",
        "EvidenceFilter",
        "EvidenceLimitation",
        "EvidenceRows",
        "EvidenceSelector",
        "EvidenceStatistic",
        "ExecutionError",
        "Finding",
        "FindingChange",
        "FindingScope",
        "FinishResearchAction",
        "FinishResearchArguments",
        "FinishResearchResult",
        "MetricAnalysisRelation",
        "MetricFormula",
        "QueryComparison",
        "QueryFilter",
        "QueryOrder",
        "QueryPeriod",
        "QueryResultSpec",
        "QuerySemanticDataAction",
        "QuerySemanticDataArguments",
        "QueryTimeSpec",
        "ReadEvidenceRowsAction",
        "ReadEvidenceRowsArguments",
        "RemainingBudget",
        "RequestClarificationAction",
        "RequestClarificationArguments",
        "ResearchAction",
        "ResearchActionType",
        "ResearchAgentInput",
        "ResearchExecutionErrorStage",
        "ResearchState",
        "ResearchStateSnapshot",
        "ResearchStateStatus",
        "ResearchTurnDecision",
        "RESEARCH_AGENT_INPUT_SCHEMA_VERSION",
        "RESEARCH_STATE_SCHEMA_VERSION",
        "RESEARCH_STATE_SNAPSHOT_SCHEMA_VERSION",
        "SearchSemanticAssetsAction",
        "SearchSemanticAssetsArguments",
        "SemanticAmbiguity",
        "SemanticContext",
        "SemanticDimension",
        "SemanticHierarchy",
        "SemanticMetric",
        "TimeRange",
        "TodoChange",
        "TodoItem",
        "ToolResult",
        "ToolResultStatus",
    ]
)
