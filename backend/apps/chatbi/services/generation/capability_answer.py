"""meta_query 的数据集能力目录回答（P0-2 分诊收口）。

基于 DatasetSchema 的真实资产生成"你能查什么 / 有哪些指标"类回答，
完全确定性、不调用模型；资产缺失时退化为能力说明而不是失败。
"""

from __future__ import annotations

from typing import Any

from apps.semantic.models.dto.dataset_schema import DatasetSchema

_MAX_LISTED_ASSETS = 20


def build_capability_answer(schema: DatasetSchema | None) -> str:
    """生成"当前数据集能查询什么"的目录式回答。"""

    dataset_name = (
        schema.data_set.name if schema is not None and schema.data_set else ""
    )
    header = (
        f"当前数据集是「{dataset_name}」，我可以在这个范围内回答取数与分析问题。"
        if dataset_name
        else "我可以回答当前数据集范围内的取数与分析问题。"
    )

    metrics = _asset_names(schema.metrics if schema is not None else [])
    dimensions = _asset_names(schema.dimensions if schema is not None else [])
    sections = [header]
    if metrics:
        listed = "、".join(metrics[:_MAX_LISTED_ASSETS])
        suffix = "等" if len(metrics) > _MAX_LISTED_ASSETS else ""
        sections.append(f"可查询的指标（共 {len(metrics)} 个）：{listed}{suffix}")
    else:
        sections.append("该数据集暂未配置语义指标。")
    if dimensions:
        listed = "、".join(dimensions[:_MAX_LISTED_ASSETS])
        suffix = "等" if len(dimensions) > _MAX_LISTED_ASSETS else ""
        sections.append(
            f"可按以下维度分组或筛选（共 {len(dimensions)} 个）：{listed}{suffix}"
        )
    sections.append(
        "示例问法：「本月的总销售额是多少」「按月份查看各月销售额」"
        "「销售额最高的5个门店」。"
        "如果你想知道某个指标的具体口径定义，可以直接问我，"
        "例如「销售额是怎么定义的」。"
    )
    return "\n".join(sections)


def build_capability_answer_from_payload(payload: dict[str, Any] | None) -> str:
    """从 DatasetSchema.model_dump() 结构构建回答，供无法导入 DTO 的调用方使用。"""

    if not isinstance(payload, dict):
        return build_capability_answer(None)
    try:
        schema = DatasetSchema.model_validate(payload)
    except Exception:
        return build_capability_answer(None)
    return build_capability_answer(schema)


def _asset_names(elements: list[Any]) -> list[str]:
    names: list[str] = []
    for element in elements:
        name = str(
            getattr(element, "name", None)
            or (element.get("name") if isinstance(element, dict) else "")
            or ""
        ).strip()
        if name:
            names.append(name)
    return names


__all__ = [
    "build_capability_answer",
    "build_capability_answer_from_payload",
]
