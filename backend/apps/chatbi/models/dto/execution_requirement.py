"""Fast/Plan 共用的执行需求契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.analysis_plan import QueryTaskSpec
from apps.chatbi.models.dto.research import ResearchRequirement


class ExecutionRoute(BaseModel):
    """执行需求的确定性路由结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["fast", "plan", "research"]
    origin: Literal["request", "research_action"] = "request"
    reasons: tuple[str, ...] = ()


class CalculationOperation(StrEnum):
    """执行需求允许声明的确定性计算操作。"""

    MERGE = "merge"
    DIFFERENCE = "difference"
    GROWTH_RATE = "growth_rate"
    SHARE = "share"
    RATIO = "ratio"
    TOPN_OTHER = "topn_other"
    PIVOT = "pivot"
    EXPR = "expr"
    CONTRIBUTION = "contribution"


class ExecutionResultContract(BaseModel):
    """声明主要结果、辅助结果和回答展示顺序。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_requirement_id: str = Field(min_length=1, max_length=128)
    supporting_requirement_ids: tuple[str, ...] = ()
    ordered_requirement_ids: tuple[str, ...] = ()
    completion_policy: Literal["require_primary"] = "require_primary"
    analysis_type: Literal[
        "standard", "fixed_drilldown", "fixed_attribution", "limited_multistep"
    ] = "standard"


class ExecutionDecompositionAudit(BaseModel):
    """记录有限多步执行需求是否由受限模型分解。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["llm"] = "llm"
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    attempts: int = Field(ge=1, le=2)
    repaired: bool = False


class DecompositionQueryDraft(BaseModel):
    """模型只声明已绑定资产的查询组合，不包含物理字段和 SQL。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    metric_refs: tuple[str, ...] = Field(min_length=1)
    dimension_refs: tuple[str, ...] = ()
    time_role: str = Field(min_length=1, max_length=64)


class DecompositionCalculationDraft(BaseModel):
    """有限多步模型可声明的白名单计算结构。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: CalculationOperation
    inputs: tuple[str, ...] = Field(min_length=1)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    numerator_ref: str | None = None
    denominator_ref: str | None = None
    result_name: str | None = Field(default=None, min_length=1, max_length=128)
    top_n: int | None = Field(default=None, gt=0)
    index_dimension_refs: tuple[str, ...] = ()
    column_dimension_ref: str | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> DecompositionCalculationDraft:
        """按操作限制草案字段，避免模型通过自由参数表达未授权逻辑。"""

        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError("DECOMPOSITION_CALCULATION_INPUT_DUPLICATED")
        if self.type is CalculationOperation.EXPR:
            raise ValueError("DECOMPOSITION_EXPR_NOT_ALLOWED")
        if self.type is CalculationOperation.MERGE and len(self.inputs) < 2:
            raise ValueError("DECOMPOSITION_MERGE_INPUTS_REQUIRED")
        if self.type in {
            CalculationOperation.DIFFERENCE,
            CalculationOperation.GROWTH_RATE,
        } and (len(self.inputs) != 2 or not self.metric_refs):
            raise ValueError("DECOMPOSITION_PERIOD_CALCULATION_INVALID")
        if self.type is CalculationOperation.SHARE and (
            len(self.inputs) != 1
            or len(self.metric_refs) != 1
            or not self.dimension_refs
        ):
            raise ValueError("DECOMPOSITION_SHARE_SHAPE_INVALID")
        if self.type is CalculationOperation.RATIO and (
            len(self.inputs) not in {1, 2}
            or not self.numerator_ref
            or not self.denominator_ref
            or not self.result_name
        ):
            raise ValueError("DECOMPOSITION_RATIO_SHAPE_INVALID")
        if self.type is CalculationOperation.TOPN_OTHER and (
            len(self.inputs) != 1
            or len(self.metric_refs) != 1
            or len(self.dimension_refs) != 1
            or self.top_n is None
        ):
            raise ValueError("DECOMPOSITION_TOPN_SHAPE_INVALID")
        if self.type is CalculationOperation.PIVOT and (
            len(self.inputs) != 1
            or len(self.metric_refs) != 1
            or not self.index_dimension_refs
            or not self.column_dimension_ref
        ):
            raise ValueError("DECOMPOSITION_PIVOT_SHAPE_INVALID")
        if self.type is CalculationOperation.CONTRIBUTION and (
            len(self.inputs) != 2
            or len(self.metric_refs) != 1
            or not self.dimension_refs
        ):
            raise ValueError("DECOMPOSITION_CONTRIBUTION_SHAPE_INVALID")
        return self


class ExecutionRequirementDraft(BaseModel):
    """有限多步模型的候选输出，校验通过后才能补齐为正式执行需求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_requirements: tuple[DecompositionQueryDraft, ...] = Field(min_length=1)
    post_calculations: tuple[DecompositionCalculationDraft, ...] = Field(min_length=1)
    result_contract: ExecutionResultContract

    @model_validator(mode="after")
    def validate_graph(self) -> ExecutionRequirementDraft:
        """草案必须形成完整 DAG，且结果契约完整覆盖全部叶子。"""

        query_ids = [item.id for item in self.query_requirements]
        calculation_ids = [item.id for item in self.post_calculations]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("DECOMPOSITION_QUERY_ID_DUPLICATED")
        if len(calculation_ids) != len(set(calculation_ids)):
            raise ValueError("DECOMPOSITION_CALCULATION_ID_DUPLICATED")
        if set(query_ids) & set(calculation_ids):
            raise ValueError("DECOMPOSITION_ID_CONFLICT")
        known_ids = set(query_ids) | set(calculation_ids)
        dependencies = {item.id: item.inputs for item in self.post_calculations}
        for item in self.post_calculations:
            if item.id in item.inputs:
                raise ValueError("DECOMPOSITION_SELF_DEPENDENCY")
            if any(input_id not in known_ids for input_id in item.inputs):
                raise ValueError("DECOMPOSITION_INPUT_UNKNOWN")
        _validate_acyclic_dependencies(dependencies)
        declared = {
            self.result_contract.primary_requirement_id,
            *self.result_contract.supporting_requirement_ids,
        }
        ordered = self.result_contract.ordered_requirement_ids
        if not declared <= known_ids:
            raise ValueError("DECOMPOSITION_RESULT_ID_UNKNOWN")
        if ordered and (len(ordered) != len(set(ordered)) or set(ordered) != declared):
            raise ValueError("DECOMPOSITION_RESULT_ORDER_INVALID")
        if not ordered:
            raise ValueError("DECOMPOSITION_RESULT_ORDER_REQUIRED")
        consumed = {
            input_id for item in self.post_calculations for input_id in item.inputs
        }
        if declared != known_ids - consumed:
            raise ValueError("DECOMPOSITION_RESULT_LEAVES_MISMATCH")
        return self


class QueryRequirement(BaseModel):
    """单个可执行查询的完整需求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    model_ref: str = Field(min_length=1)
    metrics: tuple[dict[str, Any], ...] = Field(min_length=1)
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

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: CalculationOperation
    inputs: tuple[str, ...] = Field(min_length=1)
    join_keys: tuple[str, ...] = ()
    value_columns: tuple[str, ...] = ()
    derive: tuple[dict[str, str], ...] = ()
    options: dict[str, Any] = Field(default_factory=dict)
    # 阶段 1 只声明计算引擎已经严格实现的策略，禁止接受后再忽略。
    null_policy: Literal["preserve"] = "preserve"
    zero_division_policy: Literal["null"] = "null"

    @model_validator(mode="after")
    def validate_operation_contract(self) -> CalculationRequirement:
        """在执行需求边界校验计算输入，避免错误延迟到计算引擎。"""

        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_INPUT_DUPLICATED")
        if (
            self.type
            in {
                CalculationOperation.DIFFERENCE,
                CalculationOperation.GROWTH_RATE,
            }
            and len(self.inputs) != 2
        ):
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_TWO_INPUTS_REQUIRED")
        if self.type is CalculationOperation.MERGE and len(self.inputs) < 2:
            raise ValueError("EXECUTION_REQUIREMENT_MERGE_INPUTS_REQUIRED")
        if self.type is CalculationOperation.CONTRIBUTION and len(self.inputs) != 2:
            raise ValueError("EXECUTION_REQUIREMENT_CONTRIBUTION_TWO_INPUTS_REQUIRED")
        if (
            self.type
            in {
                CalculationOperation.RATIO,
                CalculationOperation.EXPR,
            }
            and not self.derive
        ):
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_DERIVE_REQUIRED")
        if self.type is CalculationOperation.TOPN_OTHER:
            top_n = self.options.get("top_n")
            if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
                raise ValueError("EXECUTION_REQUIREMENT_TOPN_LIMIT_REQUIRED")
        return self


class ExecutionRequirement(BaseModel):
    """Fast、Plan 和 Research 执行阶段的唯一业务输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready"]
    route: ExecutionRoute
    query_requirements: tuple[QueryRequirement, ...] = ()
    post_calculations: tuple[CalculationRequirement, ...] = ()
    result_contract: ExecutionResultContract | None = None
    decomposition: ExecutionDecompositionAudit | None = None
    research_requirement: ResearchRequirement | None = None
    runtime: dict[str, Any] = Field(default_factory=dict)
    asset_snapshot: dict[str, Any] = Field(default_factory=dict)
    unresolved: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def validate_unique_requirement_ids(self) -> ExecutionRequirement:
        """统一校验 ID、依赖图和 Fast/Plan 路由不变量。"""

        query_ids = [item.id for item in self.query_requirements]
        calculation_ids = [item.id for item in self.post_calculations]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("EXECUTION_REQUIREMENT_QUERY_ID_DUPLICATED")
        if len(calculation_ids) != len(set(calculation_ids)):
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_ID_DUPLICATED")
        if set(query_ids) & set(calculation_ids):
            raise ValueError("EXECUTION_REQUIREMENT_ID_CONFLICT")
        known_ids = set(query_ids) | set(calculation_ids)
        dependencies = {item.id: item.inputs for item in self.post_calculations}
        for item in self.post_calculations:
            if item.id in item.inputs:
                raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_SELF_DEPENDENCY")
            if any(input_id not in known_ids for input_id in item.inputs):
                raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_INPUT_UNKNOWN")
        _validate_acyclic_dependencies(dependencies)
        if (
            self.status == "ready"
            and self.route.mode in {"fast", "plan"}
            and not self.query_requirements
        ):
            raise ValueError("EXECUTION_REQUIREMENT_QUERY_REQUIRED")
        if self.route.mode == "fast" and (
            len(self.query_requirements) != 1 or self.post_calculations
        ):
            raise ValueError("EXECUTION_REQUIREMENT_FAST_SHAPE_INVALID")
        if self.route.mode == "research":
            if self.research_requirement is None:
                raise ValueError("EXECUTION_REQUIREMENT_RESEARCH_REQUIRED")
            if (
                self.query_requirements
                or self.post_calculations
                or self.result_contract is not None
                or self.decomposition is not None
            ):
                raise ValueError("EXECUTION_REQUIREMENT_RESEARCH_SHAPE_INVALID")
        elif self.research_requirement is not None:
            raise ValueError("EXECUTION_REQUIREMENT_RESEARCH_NOT_ALLOWED")
        if self.result_contract is not None:
            declared = {
                self.result_contract.primary_requirement_id,
                *self.result_contract.supporting_requirement_ids,
            }
            if len(declared) != 1 + len(
                self.result_contract.supporting_requirement_ids
            ):
                raise ValueError("EXECUTION_REQUIREMENT_RESULT_ID_DUPLICATED")
            if not declared <= known_ids:
                raise ValueError("EXECUTION_REQUIREMENT_RESULT_ID_UNKNOWN")
            ordered = self.result_contract.ordered_requirement_ids
            if ordered and (len(ordered) != len(set(ordered)) or set(ordered) != declared):
                raise ValueError("EXECUTION_REQUIREMENT_RESULT_ORDER_INVALID")
            consumed = {
                input_id
                for item in self.post_calculations
                for input_id in item.inputs
            }
            leaves = known_ids - consumed
            if declared != leaves:
                raise ValueError("EXECUTION_REQUIREMENT_RESULT_LEAVES_MISMATCH")
        if self.route.mode == "plan":
            if (
                len(self.query_requirements) == 1
                and not self.post_calculations
                and self.route.origin != "research_action"
            ):
                raise ValueError("EXECUTION_REQUIREMENT_PLAN_SHAPE_INVALID")
            if (
                len(self.query_requirements) > 1
                and not self.post_calculations
                and self.result_contract is None
            ):
                raise ValueError("EXECUTION_REQUIREMENT_MULTI_QUERY_RESULT_UNRESOLVED")
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
    return int(value)


def _validate_acyclic_dependencies(
    dependencies: dict[str, tuple[str, ...]],
) -> None:
    """计算需求依赖只能形成有向无环图。"""

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("EXECUTION_REQUIREMENT_CALCULATION_CYCLE")
        if node_id in visited:
            return
        visiting.add(node_id)
        for input_id in dependencies.get(node_id, ()):
            if input_id in dependencies:
                visit(input_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for calculation_id in dependencies:
        visit(calculation_id)


__all__ = [
    "CalculationRequirement",
    "CalculationOperation",
    "DecompositionCalculationDraft",
    "DecompositionQueryDraft",
    "ExecutionDecompositionAudit",
    "ExecutionRequirement",
    "ExecutionRequirementDraft",
    "ExecutionResultContract",
    "ExecutionRoute",
    "QueryRequirement",
    "execution_requirement_from_state",
    "query_requirement_to_spec",
]
