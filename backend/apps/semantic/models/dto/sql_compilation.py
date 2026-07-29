from dataclasses import dataclass, field
from typing import Any

from apps.semantic.models.dto.dataset_schema import DatasetSchema


@dataclass(frozen=True, slots=True)
class SemanticQueryCompileRequest:
    """跨领域调用 Semantic 编译器时使用的稳定请求。"""

    workspace_id: int
    dataset_id: int
    question: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    repair_context: dict[str, Any] = field(default_factory=dict)
    order_by: list[dict[str, Any]] = field(default_factory=list)
    limit: int | None = None
    time_bucket: dict[str, Any] | None = None
    select_mode: str = "aggregate"
    having: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SemanticUsedAsset:
    """SQL 编译结果实际使用的语义资产。"""

    asset_type: str
    asset_id: int
    biz_name: str


@dataclass(frozen=True, slots=True)
class SemanticQueryCompileResult:
    """Semantic 编译结果及其使用的数据集 Schema。"""

    dataset_id: int
    sql: str
    tables: list[str]
    metrics: list[str]
    dimensions: list[str]
    schema: DatasetSchema
    datasource_id: int | None = None
    used_assets: list[SemanticUsedAsset] = field(default_factory=list)


__all__ = [
    "SemanticQueryCompileRequest",
    "SemanticQueryCompileResult",
    "SemanticUsedAsset",
]
