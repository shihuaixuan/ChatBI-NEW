"""候选资产后的语义解析 JSON 契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.models.dto.execution_requirement import CalculationOperation


class SemanticParseAssetRef(BaseModel):
    """语义解析结果中的候选资产引用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1)


class SemanticParseFilter(BaseModel):
    """语义解析结果中的筛选条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_ref: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    value: Any
    stage: Literal["where", "having"] = "where"


class SemanticParseTimeFilter(BaseModel):
    """保留用户原始时间表达，不在本阶段绑定时间资产。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: str = Field(min_length=1)
    role: str = Field(min_length=1)


class SemanticParseOrderBy(BaseModel):
    """语义解析结果中的排序条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_ref: str = Field(min_length=1)
    direction: Literal["asc", "desc"]


class SemanticParseCalculation(BaseModel):
    """语义解析结果中的计算要求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: CalculationOperation
    current_time_role: str | None = None
    previous_time_role: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SemanticParseUnresolved(BaseModel):
    """需要后续澄清或无法安全确定的语义内容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    candidate_refs: list[str] = Field(default_factory=list)


class SemanticParseOutput(BaseModel):
    """候选资产后的语义解析结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["resolved", "needs_clarification", "missed"]
    measures: list[SemanticParseAssetRef] = Field(default_factory=list)
    group_by: list[SemanticParseAssetRef] = Field(default_factory=list)
    filters: list[SemanticParseFilter] = Field(default_factory=list)
    time_filters: list[SemanticParseTimeFilter] = Field(default_factory=list)
    order_by: list[SemanticParseOrderBy] = Field(default_factory=list)
    limit: int | None = Field(default=None, gt=0)
    calculations: list[SemanticParseCalculation] = Field(default_factory=list)
    unresolved: list[SemanticParseUnresolved] = Field(default_factory=list)


__all__ = [
    "SemanticParseAssetRef",
    "SemanticParseCalculation",
    "SemanticParseFilter",
    "SemanticParseOrderBy",
    "SemanticParseOutput",
    "SemanticParseTimeFilter",
    "SemanticParseUnresolved",
]
