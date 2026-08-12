"""Semantic 公共 Tool 的可信上下文与跨工具范围。"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval import (
    ExecutableAssetReference,
    RetrievalDecisionStatus,
    RetrievalRequest,
)
from apps.temporal import derive_time_bucket
from apps.tool.tools.context import TrustedToolContext


class SemanticCompileFilter(BaseModel):
    """由语义绑定确定的可信筛选条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: int = Field(gt=0)
    operator: str = "="
    value: Any


class SemanticCompileOrderBy(BaseModel):
    """由查询形态绑定到已选资产的可信排序条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: int = Field(gt=0)
    direction: str = "desc"


class SemanticCompileTimeBucket(BaseModel):
    """由查询形态和已绑定时间维度生成的可信时间分桶。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_id: int = Field(gt=0)
    grain: Literal["day", "week", "month", "quarter", "year"]


class SemanticCompileTemporalPlan(BaseModel):
    """独立承载时间筛选和时间分组，禁止模型自行补充时间参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filters: tuple[SemanticCompileFilter, ...] = ()
    time_bucket: SemanticCompileTimeBucket | None = None


class SemanticCompilePlan(BaseModel):
    """由服务端语义决策生成、供编译工具直接使用的资产计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_asset_ids: tuple[int, ...] = ()
    dimension_asset_ids: tuple[int, ...] = ()
    filters: tuple[SemanticCompileFilter, ...] = ()
    temporal_plan: SemanticCompileTemporalPlan = Field(
        default_factory=SemanticCompileTemporalPlan
    )
    order_by: tuple[SemanticCompileOrderBy, ...] = ()
    limit: int | None = Field(default=None, gt=0, le=1000)
    intent_type: str = "metric_query"
    query_shape: dict[str, Any] = Field(default_factory=dict)


class SemanticAssetScope(BaseModel):
    """语义检索成功后由服务端保存的 SQL 编译范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    datasource_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    retrieval_id: str = Field(min_length=1)
    decision_status: RetrievalDecisionStatus
    allowed_assets: tuple[ExecutableAssetReference, ...] = ()
    authorized_tables: tuple[str, ...] = ()
    normalized_time_range: dict[str, Any] | None = None
    compile_plan: SemanticCompilePlan | None = None
    permission_version: str | None = None


class SemanticToolContext(TrustedToolContext, Protocol):
    """Semantic Tool 可读取的服务端可信输入。"""

    @property
    def semantic_retrieval_request(self) -> RetrievalRequest | None: ...

    @property
    def semantic_asset_scope(self) -> SemanticAssetScope | None: ...

    @property
    def semantic_default_limit(self) -> int: ...


def project_semantic_compile_plan(
    slot_bindings: dict[str, Any],
    intent: dict[str, Any] | None = None,
) -> SemanticCompilePlan:
    """把服务端 slot_bindings 投影成编译工具所需的最小可信参数。"""

    metrics = slot_bindings.get("metrics") or []
    dimensions = slot_bindings.get("group_dimensions") or []
    dimension_filters = slot_bindings.get("dimension_filters") or []
    time_dimensions = slot_bindings.get("time_dimensions") or []
    time_filters = slot_bindings.get("time_filters") or []
    if not all(
        isinstance(items, list)
        for items in (
            metrics,
            dimensions,
            dimension_filters,
            time_dimensions,
            time_filters,
        )
    ):
        raise ValueError("SEMANTIC_SLOT_BINDINGS_INVALID")

    metric_asset_ids = _binding_asset_ids(metrics, "METRIC")
    dimension_asset_ids = _binding_asset_ids(dimensions, "DIMENSION")
    intent_payload = intent if isinstance(intent, dict) else {}
    query_shape = (
        dict(intent_payload.get("query_shape"))
        if isinstance(intent_payload.get("query_shape"), dict)
        else {}
    )
    time_dimension_ids = _binding_asset_ids(time_dimensions, "DIMENSION")
    if not time_dimension_ids:
        time_dimension_ids = _binding_asset_ids(time_filters, "DIMENSION")
    raw_time_bucket = derive_time_bucket(query_shape, time_dimension_ids)
    return SemanticCompilePlan(
        metric_asset_ids=metric_asset_ids,
        dimension_asset_ids=dimension_asset_ids,
        filters=_compile_filters(dimension_filters),
        temporal_plan=SemanticCompileTemporalPlan(
            filters=_compile_filters(time_filters),
            time_bucket=(
                SemanticCompileTimeBucket.model_validate(raw_time_bucket)
                if raw_time_bucket is not None
                else None
            ),
        ),
        order_by=_compile_order_by(metric_asset_ids, dimension_asset_ids, query_shape),
        limit=_compile_limit(query_shape),
        intent_type=str(intent_payload.get("intent_type") or "metric_query"),
        query_shape=query_shape,
    )


def _compile_order_by(
    metric_asset_ids: tuple[int, ...],
    dimension_asset_ids: tuple[int, ...],
    query_shape: dict[str, Any],
) -> tuple[SemanticCompileOrderBy, ...]:
    if not bool(query_shape.get("needs_order_by")):
        return ()
    asset_ids = metric_asset_ids or dimension_asset_ids
    if not asset_ids:
        return ()
    return (
        SemanticCompileOrderBy(
            asset_id=asset_ids[0],
            direction=(
                "asc"
                if str(query_shape.get("order_direction") or "").lower() == "asc"
                else "desc"
            ),
        ),
    )


def _compile_limit(query_shape: dict[str, Any]) -> int | None:
    value = query_shape.get("limit")
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 1000 else None


def _binding_asset_ids(items: list[Any], asset_type: str) -> tuple[int, ...]:
    result: list[int] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("SEMANTIC_SLOT_BINDING_ITEM_INVALID")
        if str(item.get("asset_type") or "").upper() != asset_type:
            raise ValueError("SEMANTIC_SLOT_BINDING_ASSET_TYPE_INVALID")
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("SEMANTIC_SLOT_BINDING_ASSET_ID_INVALID")
        if asset_id not in seen:
            result.append(asset_id)
            seen.add(asset_id)
    return tuple(result)


def _compile_filters(items: list[Any]) -> tuple[SemanticCompileFilter, ...]:
    result: list[SemanticCompileFilter] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("SEMANTIC_SLOT_FILTER_INVALID")
        if str(item.get("asset_type") or "").upper() != "DIMENSION":
            raise ValueError("SEMANTIC_SLOT_FILTER_ASSET_TYPE_INVALID")
        if "value" not in item or item.get("value") is None:
            raise ValueError("SEMANTIC_SLOT_FILTER_VALUE_REQUIRED")
        result.append(
            SemanticCompileFilter.model_validate(
                {
                    "asset_id": item.get("asset_id"),
                    "operator": item.get("operator") or "=",
                    "value": item.get("value"),
                }
            )
        )
    return tuple(result)


__all__ = [
    "SemanticAssetScope",
    "SemanticCompileFilter",
    "SemanticCompileOrderBy",
    "SemanticCompilePlan",
    "SemanticCompileTemporalPlan",
    "SemanticCompileTimeBucket",
    "SemanticToolContext",
    "project_semantic_compile_plan",
]
