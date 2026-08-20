"""候选资产后的语义解析 JSON 契约。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

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


class SemanticParseDrilldownLevel(BaseModel):
    """固定下钻的一层，维度引用按从粗到细的顺序累积。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    dimension_refs: tuple[str, ...] = Field(min_length=1)


class SemanticParseFixedDrilldown(BaseModel):
    """执行前即可完整确定的固定下钻。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["fixed_drilldown"] = "fixed_drilldown"
    metric_refs: tuple[str, ...] = Field(min_length=1)
    levels: tuple[SemanticParseDrilldownLevel, ...] = Field(min_length=1)
    include_total: bool = False
    primary_level: str = Field(min_length=1, max_length=128)


class SemanticParseFixedAttribution(BaseModel):
    """加法指标在两个明确时间范围之间的固定维度贡献归因。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["fixed_attribution"] = "fixed_attribution"
    metric_ref: str = Field(min_length=1)
    dimension_ref: str = Field(min_length=1)
    current_time_role: str = Field(default="current", min_length=1)
    previous_time_role: str = Field(default="previous", min_length=1)
    method: Literal["additive_change_contribution"] = (
        "additive_change_contribution"
    )


class SemanticParseDynamicResearch(BaseModel):
    """后续查询方向依赖中间结果的动态分析目标。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["dynamic_research"] = "dynamic_research"
    goal: str = Field(min_length=1, max_length=1000)
    reason: Literal[
        "result_driven_filter",
        "result_driven_dimension",
        "open_ended_cause",
        "data_driven_stop_condition",
    ]


class SemanticParseLimitedMultiStep(BaseModel):
    """资产已绑定、执行前可完整确定，但固定规则无法唯一展开的有限多步任务。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["limited_multistep"] = "limited_multistep"
    objective: str = Field(min_length=1, max_length=1000)
    metric_refs: tuple[str, ...] = Field(min_length=1)
    dimension_refs: tuple[str, ...] = ()
    allowed_time_roles: tuple[str, ...] = Field(min_length=1)
    requested_outputs: tuple[str, ...] = Field(min_length=1)


SemanticParseMultiStep = Annotated[
    SemanticParseFixedDrilldown
    | SemanticParseFixedAttribution
    | SemanticParseLimitedMultiStep
    | SemanticParseDynamicResearch,
    Field(discriminator="type"),
]


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
    multi_step: SemanticParseMultiStep | None = None
    unresolved: list[SemanticParseUnresolved] = Field(default_factory=list)


__all__ = [
    "SemanticParseAssetRef",
    "SemanticParseCalculation",
    "SemanticParseFilter",
    "SemanticParseFixedAttribution",
    "SemanticParseFixedDrilldown",
    "SemanticParseDrilldownLevel",
    "SemanticParseDynamicResearch",
    "SemanticParseLimitedMultiStep",
    "SemanticParseMultiStep",
    "SemanticParseOrderBy",
    "SemanticParseOutput",
    "SemanticParseTimeFilter",
    "SemanticParseUnresolved",
]
