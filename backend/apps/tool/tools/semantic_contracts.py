"""Semantic 公共 Tool 的可信上下文与跨工具范围。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval import (
    ExecutableAssetReference,
    RetrievalDecisionStatus,
    RetrievalRequest,
)
from apps.tool.tools.context import TrustedToolContext


class SemanticCompileFilter(BaseModel):
    """由语义绑定确定的可信筛选条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: int = Field(gt=0)
    operator: str = "="
    value: Any


class SemanticCompilePlan(BaseModel):
    """由服务端语义决策生成、供编译工具直接使用的资产计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_asset_ids: tuple[int, ...] = ()
    dimension_asset_ids: tuple[int, ...] = ()
    filters: tuple[SemanticCompileFilter, ...] = ()


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


def build_semantic_compile_plan(
    slot_bindings: dict[str, Any],
) -> SemanticCompilePlan:
    """把服务端 slot_bindings 投影成编译工具所需的最小可信参数。"""

    metrics = slot_bindings.get("metrics") or []
    dimensions = slot_bindings.get("group_dimensions") or []
    dimension_filters = slot_bindings.get("dimension_filters") or []
    time_filters = slot_bindings.get("time_filters") or []
    if not all(
        isinstance(items, list)
        for items in (metrics, dimensions, dimension_filters, time_filters)
    ):
        raise ValueError("SEMANTIC_SLOT_BINDINGS_INVALID")

    return SemanticCompilePlan(
        metric_asset_ids=_binding_asset_ids(metrics, "METRIC"),
        dimension_asset_ids=_binding_asset_ids(dimensions, "DIMENSION"),
        filters=_compile_filters([*dimension_filters, *time_filters]),
    )


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
    "SemanticCompilePlan",
    "SemanticToolContext",
    "build_semantic_compile_plan",
]
