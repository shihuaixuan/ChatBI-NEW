"""Fast、Plan、Research 共用的 Evidence 数据契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.execution_requirement import SemanticOperation


class AnalysisEvidenceLevel(StrEnum):
    GOVERNED = "governed"
    EXPLORATORY = "exploratory"


class AnalysisEvidenceColumn(BaseModel):
    """逻辑资产到 ResultSet 字段的受控映射。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

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
    result_field: str | None = Field(default=None, min_length=1, max_length=256)


class AnalysisEvidenceStatistics(BaseModel):
    """Evidence 的有界统计，不保存完整结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row_count: int = Field(ge=0)
    truncated: bool = False


class AnalysisEvidenceDependency(BaseModel):
    """当前 Evidence 对前置 Evidence 的依赖。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=256)
    relation: str = Field(min_length=1, max_length=128)
    source_iteration: int = Field(ge=0)


class AnalysisEvidenceVersion(BaseModel):
    """Evidence 生成时的语义、Scope 和权限版本快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(gt=0)
    contract_version: int = Field(gt=0)
    schema_fingerprint: str = Field(min_length=1, max_length=256)
    scope_fingerprint: str = Field(min_length=1, max_length=256)
    permission_fingerprint: str = Field(min_length=1, max_length=256)


class AnalysisEvidence(BaseModel):
    """三种分析模式共用的执行事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=128)
    evidence_id: str = Field(min_length=1, max_length=256)
    mode: Literal["agent", "fast", "plan", "research"]
    plan_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)
    result_set_id: str = Field(min_length=1, max_length=256)
    iteration: int = Field(ge=0)
    purpose: str = Field(min_length=1, max_length=1000)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    time_roles: tuple[str, ...] = ()
    operations: tuple[SemanticOperation, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    logical_columns: tuple[AnalysisEvidenceColumn, ...] = Field(min_length=1)
    statistics: AnalysisEvidenceStatistics
    sample_rows: tuple[dict[str, Any], ...] = ()
    dependencies: tuple[AnalysisEvidenceDependency, ...] = ()
    evidence_level: AnalysisEvidenceLevel = AnalysisEvidenceLevel.GOVERNED
    version_snapshot: AnalysisEvidenceVersion
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> AnalysisEvidence:
        if len(self.metric_refs) != len(set(self.metric_refs)):
            raise ValueError("ANALYSIS_EVIDENCE_METRIC_DUPLICATED")
        if len(self.dimension_refs) != len(set(self.dimension_refs)):
            raise ValueError("ANALYSIS_EVIDENCE_DIMENSION_DUPLICATED")
        if len(self.time_roles) != len(set(self.time_roles)):
            raise ValueError("ANALYSIS_EVIDENCE_TIME_ROLE_DUPLICATED")
        dependency_ids = tuple(item.evidence_id for item in self.dependencies)
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("ANALYSIS_EVIDENCE_DEPENDENCY_DUPLICATED")
        if self.evidence_id in dependency_ids:
            raise ValueError("ANALYSIS_EVIDENCE_SELF_DEPENDENCY")
        if any(
            item.source_iteration >= self.iteration for item in self.dependencies
        ):
            raise ValueError("ANALYSIS_EVIDENCE_DEPENDENCY_MUST_PRECEDE")
        return self


__all__ = [
    "AnalysisEvidence",
    "AnalysisEvidenceColumn",
    "AnalysisEvidenceDependency",
    "AnalysisEvidenceLevel",
    "AnalysisEvidenceStatistics",
    "AnalysisEvidenceVersion",
]
