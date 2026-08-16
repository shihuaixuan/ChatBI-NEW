"""图表 spec 的规则推导，LLM 不决定字段和图表类型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChartType = Literal["table", "bar", "line", "area", "pie", "combo", "pivot", "KPI"]


class ChartSpec(BaseModel):
    """前端图表渲染契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ChartType
    title: str = ""
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None
    columns: list[dict[str, str]] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


def derive_chart_spec(data: Any) -> ChartSpec:
    """根据结果形状和已确认意图选择图表。"""

    execution = dict(getattr(data, "execution", {}) or {})
    intent = dict(getattr(data, "intent", {}) or {})
    rows = [row for row in (getattr(data, "rows", []) or []) if isinstance(row, dict)]
    fields = [str(field) for field in execution.get("fields") or [] if str(field).strip()]
    if not fields and rows:
        fields = list(dict.fromkeys(str(key) for row in rows for key in row))
    title = str(intent.get("chart_title") or intent.get("title") or "查询结果")
    columns = [{"name": field, "value": field} for field in fields]
    if not rows or not fields:
        return ChartSpec(type="table", title=title, columns=columns, data=rows[:100])

    numeric = [field for field in fields if any(_number(row.get(field)) is not None for row in rows)]
    dimensions = [field for field in fields if field not in numeric]
    shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
    shape_name = str(shape.get("shape") or shape.get("query_shape") or intent.get("query_shape") or "")
    intent_type = str(intent.get("intent_type") or "")
    if shape_name == "pivot" or intent_type in {"pivot", "pivot_analysis"}:
        return ChartSpec(type="pivot", title=title, x=dimensions[0] if dimensions else None, y=numeric, columns=columns, data=rows[:100])
    if len(rows) == 1 and not dimensions and numeric:
        return ChartSpec(type="KPI", title=title, y=numeric, columns=columns, data=rows[:100])
    if not dimensions or not numeric:
        return ChartSpec(type="table", title=title, columns=columns, data=rows[:100])

    x_field = _time_field(dimensions, intent)
    if len(numeric) >= 2 and (intent.get("chart_type") == "combo" or intent_type in {"comparison", "comparison_analysis"}):
        return ChartSpec(type="combo", title=title, x=x_field, y=numeric, series=dimensions[1] if len(dimensions) > 1 else None, columns=columns, data=rows[:100])
    if intent_type in {"share_analysis", "composition", "share"} or intent.get("chart_type") == "pie":
        return ChartSpec(type="pie", title=title, x=dimensions[0], y=numeric[:1], columns=columns, data=rows[:100])
    if x_field:
        chart_type = "area" if intent.get("chart_type") == "area" else "line"
        return ChartSpec(type=chart_type, title=title, x=x_field, y=numeric, series=dimensions[1] if len(dimensions) > 1 else None, columns=columns, data=rows[:100], options={"multi_series": len(numeric) > 1 or len(dimensions) > 1})
    bar_mode = "stacked" if intent.get("stacked") or intent.get("bar_mode") == "stacked" else "grouped"
    return ChartSpec(type="bar", title=title, x=dimensions[0], y=numeric, columns=columns, data=rows[:100], options={"mode": bar_mode})


def _time_field(fields: list[str], intent: dict[str, Any]) -> str | None:
    if isinstance(intent.get("time_dimension"), str):
        return intent["time_dimension"] if intent["time_dimension"] in fields else None
    for field in fields:
        normalized = field.lower()
        if any(token in normalized for token in ("date", "time", "month", "week", "day", "year", "period")):
            return field
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return None


__all__ = ["ChartSpec", "ChartType", "derive_chart_spec"]
