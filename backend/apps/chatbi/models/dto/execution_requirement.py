"""Fast/Plan 共用的执行需求契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.analysis_plan import QueryTaskSpec


class ExecutionRoute(BaseModel):
    """执行需求的确定性路由结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["fast", "plan", "research"]
    reasons: tuple[str, ...] = ()


class QueryRequirement(BaseModel):
    """单个可执行查询的完整需求。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    model_ref: str = Field(min_length=1)
    metrics: tuple[dict[str, Any], ...] = ()
    group_by: tuple[dict[str, Any], ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    time: dict[str, Any] | None = None
    order_by: tuple[dict[str, Any], ...] = ()
    limit: int | None = Field(default=None, gt=0)
    having: tuple[dict[str, Any], ...] = ()
    query_shape: dict[str, Any] = Field(default_factory=dict)
    time_offset: dict[str, Any] | None = None


class CalculationRequirement(BaseModel):
    """查询完成后的确定性计算需求。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1)
    inputs: tuple[str, ...] = ()
    join_keys: tuple[str, ...] = ()
    metric_ref: str | None = None
    current_input: str | None = None
    previous_input: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ExecutionRequirement(BaseModel):
    """Fast/Plan 执行阶段的唯一业务输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready"] | str
    route: ExecutionRoute
    query_requirements: tuple[QueryRequirement, ...] = ()
    post_calculations: tuple[CalculationRequirement, ...] = ()
    runtime: dict[str, Any] = Field(default_factory=dict)
    asset_snapshot: dict[str, Any] = Field(default_factory=dict)
    unresolved: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def validate_unique_requirement_ids(self) -> ExecutionRequirement:
        """需求 ID 必须唯一，后续计划节点只能按 ID 精确引用。"""

        query_ids = [item.id for item in self.query_requirements]
        calculation_ids = [item.id for item in self.post_calculations]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("EXECUTION_REQUIREMENT_QUERY_ID_DUPLICATED")
        if len(calculation_ids) != len(set(calculation_ids)):
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_ID_DUPLICATED")
        return self

    def require_ready(self, mode: str) -> ExecutionRequirement:
        """校验当前模式只能执行匹配的、已经准备好的需求。"""

        if self.status != "ready":
            raise ValueError("EXECUTION_REQUIREMENT_NOT_READY")
        if self.route.mode != mode:
            raise ValueError("EXECUTION_REQUIREMENT_ROUTE_MISMATCH")
        if self.unresolved:
            raise ValueError("EXECUTION_REQUIREMENT_UNRESOLVED")
        return self


def execution_requirement_from_state(state: dict[str, Any]) -> ExecutionRequirement:
    """从运行态读取并校验执行需求，不允许调用方静默构造默认值。"""

    payload = state.get("execution_requirement")
    if not isinstance(payload, dict):
        raise ValueError("EXECUTION_REQUIREMENT_STATE_REQUIRED")
    try:
        return ExecutionRequirement.model_validate(payload)
    except ValueError as exc:
        raise ValueError("EXECUTION_REQUIREMENT_STATE_INVALID") from exc


def query_requirement_to_spec(
    requirement: QueryRequirement,
    *,
    dataset_id: int,
) -> QueryTaskSpec:
    """把执行需求转换成现有语义查询任务规格。"""

    if dataset_id <= 0:
        raise ValueError("EXECUTION_REQUIREMENT_DATASET_REQUIRED")
    metric_ids = tuple(_asset_id(item, "metric") for item in requirement.metrics)
    dimension_ids = tuple(_asset_id(item, "dimension") for item in requirement.group_by)
    time = requirement.time or {}
    normalized_time = time.get("normalized")
    if time.get("expression") and (
        not isinstance(normalized_time, dict)
        or normalized_time.get("kind") == "unsupported"
    ):
        raise ValueError("EXECUTION_REQUIREMENT_TIME_NOT_NORMALIZED")
    where_filters = tuple(
        item
        for item in requirement.filters
        if str(item.get("stage") or "where").lower() != "having"
    )
    having = requirement.having or tuple(
        item
        for item in requirement.filters
        if str(item.get("stage") or "where").lower() == "having"
    )
    query_shape = requirement.query_shape
    return QueryTaskSpec(
        dataset_id=dataset_id,
        metric_ids=metric_ids,
        dimension_ids=dimension_ids,
        filters=where_filters,
        time_range=normalized_time if isinstance(normalized_time, dict) else None,
        time_dimension_id=_optional_asset_id(time.get("dimension_id")),
        time_grain=(str(time.get("grain")) if time.get("grain") else None),
        select_mode=(
            str(query_shape.get("select_mode"))
            if query_shape.get("select_mode")
            else None
        ),
        query_shape=(
            str(query_shape.get("shape") or query_shape.get("query_shape"))
            if query_shape.get("shape") or query_shape.get("query_shape")
            else "single_query"
        ),
        order_by=requirement.order_by,
        limit=requirement.limit,
        having=having,
        time_offset=requirement.time_offset,
    )


def _asset_id(item: dict[str, Any], asset_type: str) -> int:
    asset_id = _optional_asset_id(item.get("asset_id"))
    if asset_id is None:
        raise ValueError(f"EXECUTION_REQUIREMENT_{asset_type.upper()}_ID_REQUIRED")
    return asset_id


def _optional_asset_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


__all__ = [
    "CalculationRequirement",
    "ExecutionRequirement",
    "ExecutionRoute",
    "QueryRequirement",
    "execution_requirement_from_state",
    "query_requirement_to_spec",
]
