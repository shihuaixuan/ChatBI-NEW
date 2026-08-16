"""回答口径卡片的确定性构造。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaliberCard(BaseModel):
    """前端可展开的回答口径说明。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[Any] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    time: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    certified: bool = False


def build_caliber_card(data: Any) -> CaliberCard:
    """从语义理解、执行元数据和 SQL 生成口径卡片。"""

    intent = dict(getattr(data, "intent", {}) or {})
    execution = dict(getattr(data, "execution", {}) or {})
    semantic = dict(getattr(data, "semantic_context", {}) or {})
    metrics = _metrics(intent, semantic)
    dimensions = _dimensions(intent, execution, semantic)
    filters = _filters(intent, semantic)
    time = _time(intent)
    sql = execution.get("sql")
    source = str(execution.get("sql_source") or "compiled")
    if "certified" in semantic:
        certified = bool(semantic["certified"])
    else:
        certified = bool(
            execution.get("certified")
            or (
                source not in {"manual", "assisted_fallback"}
                and semantic.get("semantic_enforcement") in {None, "STRICT"}
            )
        )
    return CaliberCard(
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        time=time,
        sql=str(sql) if isinstance(sql, str) and sql else None,
        certified=certified,
    )


def _metrics(intent: dict[str, Any], semantic: dict[str, Any]) -> list[dict[str, Any]]:
    values = intent.get("metrics") or intent.get("metric_mentions") or semantic.get("metrics") or []
    if isinstance(values, dict):
        values = [values]
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            metric = dict(item)
            metric.setdefault("name", metric.get("label") or metric.get("metric_id"))
            result.append(metric)
        elif str(item).strip():
            result.append({"name": str(item), "definition": None})
    return result


def _dimensions(intent: dict[str, Any], execution: dict[str, Any], semantic: dict[str, Any]) -> list[Any]:
    values = intent.get("dimensions") or intent.get("dimension_mentions") or intent.get("group_bys") or semantic.get("dimensions") or []
    if isinstance(values, dict):
        values = [values]
    if values:
        return list(values)
    fields = execution.get("fields") or []
    return [str(field) for field in fields if str(field).strip() and not _looks_numeric_field(str(field))]


def _filters(intent: dict[str, Any], semantic: dict[str, Any]) -> list[dict[str, Any]]:
    values = intent.get("filters") or intent.get("dimension_filters") or semantic.get("filters") or []
    if isinstance(values, dict):
        values = [values]
    result = []
    for value in values:
        if isinstance(value, dict):
            item = dict(value)
            # 保留原始词和 canonical_value，供前端解释归一过程。
            if "original_term" in item and "value_mapping" not in item:
                item["value_mapping"] = {
                    "original_term": item["original_term"],
                    "canonical_value": item.get("value") or item.get("canonical_value"),
                }
            result.append(item)
    return result


def _time(intent: dict[str, Any]) -> dict[str, Any]:
    value = intent.get("time_ranges") or intent.get("time_range") or {}
    if isinstance(value, list):
        return {"ranges": value}
    return dict(value) if isinstance(value, dict) else {"value": value}


def _looks_numeric_field(field: str) -> bool:
    normalized = field.lower()
    return any(token in normalized for token in ("count", "amount", "gmv", "value", "rate", "percent", "sum", "avg", "total"))


__all__ = ["CaliberCard", "build_caliber_card"]
