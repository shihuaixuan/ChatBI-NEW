"""语义查询计划在阶段0使用的状态和原因码契约。"""

from enum import StrEnum

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class SemanticPlanStatus(StrEnum):
    """语义计划的终态，后续规划服务必须复用这些值。"""

    PROVEN = "PROVEN"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    INFEASIBLE = "INFEASIBLE"
    INVALID_CONTRACT = "INVALID_CONTRACT"


class SemanticValidationReasonCode(StrEnum):
    """完整语义模型的稳定验证原因码。"""

    SEMANTIC_ASSET_VERSION_CHANGED = "SEMANTIC_ASSET_VERSION_CHANGED"
    LOGICAL_DIMENSION_AMBIGUOUS = "LOGICAL_DIMENSION_AMBIGUOUS"
    METRIC_GRAIN_INCOMPATIBLE = "METRIC_GRAIN_INCOMPATIBLE"
    METRIC_NOT_ADDITIVE = "METRIC_NOT_ADDITIVE"
    RELATION_PATH_INVALID = "RELATION_PATH_INVALID"
    JOIN_CAUSES_METRIC_DUPLICATION = "JOIN_CAUSES_METRIC_DUPLICATION"
    TIME_SEMANTICS_INCOMPATIBLE = "TIME_SEMANTICS_INCOMPATIBLE"
    MULTI_METRIC_GRAIN_MISMATCH = "MULTI_METRIC_GRAIN_MISMATCH"
    SEMANTIC_PLAN_PERMISSION_DENIED = "SEMANTIC_PLAN_PERMISSION_DENIED"
    SEMANTIC_CONTRACT_INCOMPLETE = "SEMANTIC_CONTRACT_INCOMPLETE"
    DIMENSION_NOT_COMPATIBLE_WITH_METRIC = "DIMENSION_NOT_COMPATIBLE_WITH_METRIC"
    METRIC_NOT_ADDITIVE_OVER_TIME = "METRIC_NOT_ADDITIVE_OVER_TIME"
    METRIC_FILTER_CONFLICT = "METRIC_FILTER_CONFLICT"


class SemanticValidationCheck(SemanticBaseDTO):
    """单项发布校验结果，供完整度报告和后续审计复用。"""

    check_type: str
    status: str
    subject_refs: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    message: str
    evidence_refs: list[str] = Field(default_factory=list)


class SemanticContractCompletenessReport(SemanticBaseDTO):
    """数据集契约发布前的完整度报告。"""

    status: str
    checks: list[SemanticValidationCheck] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    contract_version: int = 0
    schema_fingerprint: str = ""


__all__ = [
    "SemanticContractCompletenessReport",
    "SemanticPlanStatus",
    "SemanticValidationCheck",
    "SemanticValidationReasonCode",
]
