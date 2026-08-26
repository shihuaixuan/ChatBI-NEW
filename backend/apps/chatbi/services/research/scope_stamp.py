"""Semantic Scope 盖戳的共享实现（doc38 §7.4 冻结输入物化）。

路由期检索产出的 ``SemanticAssetScope`` 不携带执行所需的三项边界输入：
冻结 ``DatasetSchema`` 快照、Scope/权限指纹、可执行资产集。主路径适配器
（``orchestration/pipeline/plan_and_solve_pipeline.py``）与 shadow 双跑
（``services/research/shadow.py``）必须用**同一份实现**补齐这三项——
§1.1 同源图要求两条引擎路径对同一冻结输入得到逐字节相同的工具上下文。

本模块放在 services 层是因为依赖方向：orchestration 可以 import
services，反向不行；shadow（services）无法复用写在 pipeline 里的旧实现。
"""

from __future__ import annotations

from typing import Any

from apps.retrieval import ExecutableAssetReference, RetrievalResourceType
from apps.semantic.models.dto import DatasetSchema
from apps.tool.tools.semantic_contracts import SemanticAssetScope


def governed_asset_refs(scope: Any) -> tuple[str, ...]:
    """从冻结治理 Scope 推导可执行资产引用集合。

    指标取目标 ∪ 驱动 ∪ 贡献，维度取 Scope 维度 ∪ 贡献维度，剔除
    ``excluded_asset_refs`` 后按引用去重。口径必须覆盖 semantic_query_builder
    的 SCOPE_DENIED 门允许的全部引用，否则合法查询会因 asset_map 缺项
    被误判 UNSUPPORTED_CAPABILITY。
    """

    def _get(key: str) -> list[str]:
        value = (
            scope.get(key) if isinstance(scope, dict) else getattr(scope, key, None)
        )
        return [item for item in (value or ()) if isinstance(item, str)]

    excluded = set(_get("excluded_asset_refs"))
    refs: list[str] = []
    for key in (
        "target_metric_refs",
        "driver_metric_refs",
        "contribution_metric_refs",
        "dimension_refs",
        "contribution_dimension_refs",
    ):
        refs.extend(item for item in _get(key) if item not in excluded)
    return tuple(dict.fromkeys(refs))


def allowed_asset_references(refs: tuple[str, ...]) -> tuple[ExecutableAssetReference, ...]:
    """把 ``KIND:资产ID:模型ID`` 冻结引用物化为运行时可执行资产。"""

    references: list[ExecutableAssetReference] = []
    for ref in refs:
        parts = ref.split(":")
        if (
            len(parts) != 3
            or not parts[1].isdigit()
            or not parts[2].isdigit()
            or parts[0] not in {item.value for item in RetrievalResourceType}
        ):
            raise ValueError(f"冻结 Scope 含非法资产引用：{ref!r}")
        kind, asset_id, model_id = parts
        references.append(
            ExecutableAssetReference(
                asset_type=RetrievalResourceType(kind),
                asset_id=int(asset_id),
                model_id=int(model_id),
            )
        )
    return tuple(references)


def stamp_semantic_scope(
    *,
    scope: SemanticAssetScope | dict[str, Any],
    schema_payload: dict[str, Any],
    scope_fingerprint: str,
    permission_fingerprint: str,
    allowed_asset_refs: tuple[str, ...],
) -> dict[str, Any]:
    """把路由检索 Scope 补齐为执行边界完整的盖戳载荷。

    输入可以是模型实例或已持久化的 dict 载荷（shadow 从冻结状态读取时
    是 dict）；输出恒为 ``model_dump(mode="json")`` dict，直接可注入工具
    上下文 state。
    """

    resolved = (
        scope
        if isinstance(scope, SemanticAssetScope)
        else SemanticAssetScope.model_validate(scope)
    )
    enriched = resolved.model_copy(
        update={
            "schema_snapshot": DatasetSchema.model_validate(schema_payload),
            "scope_fingerprint": scope_fingerprint,
            "permission_fingerprint": permission_fingerprint,
            "allowed_assets": allowed_asset_references(allowed_asset_refs),
        }
    )
    return enriched.model_dump(mode="json")


__all__ = [
    "allowed_asset_references",
    "governed_asset_refs",
    "stamp_semantic_scope",
]
