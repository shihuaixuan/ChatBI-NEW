from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SemanticQueryCompileData:
    """ChatBI 发起语义 SQL 编译所需的查询计划。"""

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
class SemanticQueryUsedAsset:
    asset_type: str
    asset_id: int
    biz_name: str


@dataclass(frozen=True, slots=True)
class SemanticQueryCompileResult:
    dataset_id: int
    sql: str
    tables: list[str]
    metrics: list[str]
    dimensions: list[str]
    datasource_id: int | None
    used_assets: list[SemanticQueryUsedAsset]


__all__ = [
    "SemanticQueryCompileData",
    "SemanticQueryCompileResult",
    "SemanticQueryUsedAsset",
]
