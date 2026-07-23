"""语义查询计划（QueryPlan）绑定。

QueryPlan 是 SQL 生成的唯一事实源：检索、门控、澄清与用户选择的产物在
`bind_query_plan` 节点收敛为一个可编译、可校验的计划，SQL 生成只读计划。

槽位推导函数（`derive_semantic_slots` / `derive_order_and_limit`）是从
SQL 适配层平移过来的唯一实现，绑定器与旧路径共用同一份代码——这保证
"经计划编译"与"直接从 knowledge 编译"在既有查询形态上逐字节等价
（golden 对比测试锁定）。计划在此之上补充两类旧路径缺失的语义：

- `time.grain`：意图层识别的时间粒度，编译器据此做时间分桶（修复 A7：
  趋势查询不再退化为区间总量）；
- 维值资产翻译：命中的 VALUE 资产转换为"父维度 = 标准值"的过滤条件
  （修复 B9：VALUE 过滤此前在编译器中被静默丢弃）。
"""

from __future__ import annotations

import copy
import re
from typing import Any

from apps.workflow.capabilities.capability_matrix import decide_capability
from apps.workflow.capabilities.context import ChatBIRunContext, int_or_none
from apps.workflow.capabilities.interactions import (
    prune_dimensions_for_selected_metric,
    selected_metric_from_response,
)
from apps.workflow.schemas.v1 import QueryPlanOutput

_TIME_GRAINS = {"day", "week", "month", "quarter", "year"}


def slot_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _asset_slots(
    slot_bindings: dict[str, Any],
    selected_assets: dict[str, Any],
    key: str,
    asset_type: str,
) -> list[dict[str, Any]]:
    slots = []
    seen: set[int] = set()
    for item in [*slot_items(slot_bindings.get(key)), *slot_items(selected_assets.get(key))]:
        asset_id = int_or_none(item.get("asset_id"))
        if asset_id is None or asset_id in seen:
            continue
        seen.add(asset_id)
        slots.append(
            {
                "asset_type": item.get("asset_type") or asset_type,
                "asset_id": asset_id,
                "display_name": item.get("display_name") or item.get("name") or item.get("biz_name"),
                "operator": item.get("operator"),
                "value": item.get("value"),
            }
        )
    return slots


def _compile_filter_slots(
    slot_bindings: dict[str, Any],
    selected_assets: dict[str, Any],
) -> list[dict[str, Any]]:
    """优先使用已拆分过滤字段；旧上下文继续读取 filters。"""

    separated_filter_keys = ("value_filters", "dimension_filters", "time_filters")
    if any(key in slot_bindings for key in separated_filter_keys):
        return [
            *_asset_slots(slot_bindings, {}, "value_filters", "VALUE"),
            *_asset_slots(slot_bindings, {}, "dimension_filters", "DIMENSION"),
            *_asset_slots(slot_bindings, {}, "time_filters", "DIMENSION"),
        ]
    return _asset_slots(slot_bindings, selected_assets, "filters", "DIMENSION")


def _dimensions_without_filter_only_assets(
    dimensions: list[dict[str, Any]],
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filter_dimension_ids = {
        item.get("asset_id")
        for item in filters
        if str(item.get("asset_type") or "").upper() in {"", "DIMENSION"} and item.get("asset_id") is not None
    }
    if not filter_dimension_ids:
        return dimensions
    return [item for item in dimensions if item.get("asset_id") not in filter_dimension_ids]


def _compile_dimension_slots(
    slot_bindings: dict[str, Any],
    selected_assets: dict[str, Any],
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """新上下文只把 group_dimensions 作为 SQL 维度，避免时间维度进 GROUP BY。"""

    if "group_dimensions" in slot_bindings:
        return _asset_slots(slot_bindings, {}, "group_dimensions", "DIMENSION")
    if "business_dimensions" in selected_assets:
        return _asset_slots({}, selected_assets, "business_dimensions", "DIMENSION")
    return _dimensions_without_filter_only_assets(
        _asset_slots(slot_bindings, selected_assets, "dimensions", "DIMENSION"),
        filters,
    )


def _should_select_dimensions(intent: dict[str, Any] | None) -> bool:
    if not isinstance(intent, dict) or not intent:
        return True
    intent_type = str(intent.get("intent_type") or "").lower()
    query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
    if bool(query_shape.get("needs_group_by")):
        return True
    if intent_type in {"trend_analysis", "ranking_analysis", "comparison_analysis", "detail_query", "share_analysis"}:
        return True
    dimension_slots = intent.get("dimension_slots")
    if isinstance(dimension_slots, list):
        return any(
            isinstance(slot, dict) and str(slot.get("role") or "").lower() == "group_by"
            for slot in dimension_slots
        )
    return False


def derive_semantic_slots(knowledge: dict[str, Any], intent: dict[str, Any] | None = None) -> dict[str, Any]:
    """从知识检索输出推导编译槽位。绑定器与旧 SQL 路径共用的唯一实现。"""

    slot_bindings = knowledge.get("slot_bindings") if isinstance(knowledge.get("slot_bindings"), dict) else {}
    selected_assets = knowledge.get("selected_assets") if isinstance(knowledge.get("selected_assets"), dict) else {}
    filters = _compile_filter_slots(slot_bindings, selected_assets)
    dimensions = _compile_dimension_slots(slot_bindings, selected_assets, filters)
    if not _should_select_dimensions(intent):
        dimensions = []
    return {
        "metrics": _asset_slots(slot_bindings, selected_assets, "metrics", "METRIC"),
        "dimensions": dimensions,
        "filters": filters,
    }


def derive_order_and_limit(
    intent: dict[str, Any],
    slots: dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    """把排名查询形态绑定到已选择的指标，禁止使用未落地的自由字段。"""

    query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
    limit = int_or_none(query_shape.get("limit"))
    if limit is not None and not 1 <= limit <= 1000:
        limit = None
    if not bool(query_shape.get("needs_order_by")):
        return [], limit
    metrics = slots.get("metrics") if isinstance(slots.get("metrics"), list) else []
    if not metrics:
        return [], limit
    metric_id = int_or_none(metrics[0].get("asset_id"))
    if metric_id is None:
        return [], limit
    direction = "asc" if str(query_shape.get("order_direction") or "").lower() == "asc" else "desc"
    return [
        {
            "asset_type": "METRIC",
            "asset_id": metric_id,
            "direction": direction,
        }
    ], limit


def derive_select_mode(intent: dict[str, Any]) -> str:
    """从意图形态推导查询模式；默认保持聚合查询兼容。"""

    query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
    select_mode = str(query_shape.get("select_mode") or "").strip().lower()
    if select_mode == "detail" or str(intent.get("intent_type") or "").lower() == "detail_query":
        return "detail"
    return "aggregate"


def derive_having(intent: dict[str, Any], metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把指标阈值线索绑定为 HAVING 槽位，普通维度筛选不在这里处理。"""

    mentions = intent.get("filter_mentions")
    if not isinstance(mentions, list) or not metrics:
        return []
    slots: list[dict[str, Any]] = []
    for mention in mentions:
        if not isinstance(mention, dict) or not _is_metric_filter_mention(mention, metrics):
            continue
        metric = _metric_for_having(mention, metrics)
        if metric is None:
            continue
        value = mention.get("value")
        if value in (None, ""):
            continue
        slots.append(
            {
                "asset_type": "METRIC",
                "asset_id": metric.get("asset_id"),
                "display_name": metric.get("display_name") or metric.get("name") or metric.get("biz_name"),
                "operator": _safe_having_operator(mention.get("operator")),
                "value": value,
            }
        )
    return slots


def derive_multi_query_sub_plans(
    intent: dict[str, Any],
    metrics: list[dict[str, Any]],
    group_bys: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    having: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为比较/占比生成可复用 split 执行节点的子查询计划。"""

    intent_type = str(intent.get("intent_type") or "").strip().lower()
    if intent_type == "share_analysis":
        return _share_sub_plans(metrics, group_bys, filters, having)
    if intent_type == "comparison_analysis":
        return _comparison_sub_plans(metrics, group_bys, filters, having)
    return []


def derive_time_bucket(knowledge: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    """从意图时间粒度与已绑定时间维度推导分桶配置（A7）。"""

    query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
    grain = str(query_shape.get("time_grain") or "").strip().lower()
    if grain not in _TIME_GRAINS:
        return {}
    slot_bindings = knowledge.get("slot_bindings") if isinstance(knowledge.get("slot_bindings"), dict) else {}
    for key in ("time_dimensions", "time_filters"):
        for item in slot_items(slot_bindings.get(key)):
            dimension_id = int_or_none(item.get("asset_id"))
            if dimension_id is not None:
                return {"dimension_id": dimension_id, "grain": grain}
    return {}


def _share_sub_plans(
    metrics: list[dict[str, Any]],
    group_bys: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    having: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not metrics or not group_bys:
        return []
    return [
        _sub_plan("part", metrics, group_bys, filters, having),
        _sub_plan("total", metrics, [], filters, []),
    ]


def _comparison_sub_plans(
    metrics: list[dict[str, Any]],
    group_bys: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    having: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not metrics:
        return []
    time_filter_index = _comparison_time_filter_index(filters)
    if time_filter_index is None:
        return []
    baseline_filters = copy.deepcopy(filters)
    baseline_value = _baseline_time_value(baseline_filters[time_filter_index].get("value"))
    if baseline_value is None:
        return []
    baseline_filters[time_filter_index]["value"] = baseline_value
    return [
        _sub_plan("current", metrics, group_bys, filters, having),
        _sub_plan("baseline", metrics, group_bys, baseline_filters, having),
    ]


def _comparison_time_filter_index(filters: list[dict[str, Any]]) -> int | None:
    """定位可推导对比基准窗口的时间过滤条件。

    支持 current_period（本月/本周…→上一周期）与月对齐的 absolute_range
    （YYYY年M月 → 上一自然月）。relative_range 等窗口无法确定性平移，交由
    能力矩阵回退单查询并如实说明（见 R2/R4）。
    """

    for index, item in enumerate(filters):
        value = item.get("value")
        if _baseline_time_value(value) is not None:
            return index
    return None


def _baseline_time_value(value: Any) -> dict[str, Any] | None:
    """把当前时间窗平移为对比基准窗口；无法确定性平移时返回 None。"""

    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").lower()
    if kind == "current_period" and str(value.get("unit") or "").strip():
        baseline = {key: value[key] for key in ("unit", "timezone") if key in value}
        baseline["kind"] = "previous_period"
        baseline["unit"] = str(value.get("unit") or "").strip().lower()
        return baseline
    if kind == "absolute_range":
        return _previous_month_absolute_range(value)
    return None


def _previous_month_absolute_range(value: dict[str, Any]) -> dict[str, Any] | None:
    """月对齐的绝对区间 → 上一自然月区间（环比），非月对齐则不平移。"""

    start = _month_aligned_start(value.get("start"))
    end_exclusive = _month_aligned_start(value.get("end_exclusive"))
    if start is None or end_exclusive is None:
        return None
    # 仅处理"整月"区间：结束月恰为起始月的下一月。
    if (start[0], start[1] + 1) != (end_exclusive[0], end_exclusive[1]) and not (
        start[1] == 12 and end_exclusive == (start[0] + 1, 1)
    ):
        return None
    prev_year, prev_month = (start[0] - 1, 12) if start[1] == 1 else (start[0], start[1] - 1)
    baseline = dict(value)
    baseline["start"] = f"{prev_year:04d}-{prev_month:02d}-01"
    baseline["end_exclusive"] = f"{start[0]:04d}-{start[1]:02d}-01"
    return baseline


def _month_aligned_start(text: Any) -> tuple[int, int] | None:
    """解析 YYYY-MM-01 形态的月初日期，返回 (year, month)；否则 None。"""

    match = re.fullmatch(r"(\d{4})-(\d{2})-01", str(text or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (year, month) if 1 <= month <= 12 else None


def _sub_plan(
    role: str,
    metrics: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    having: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "role": role,
        "slots": {
            "metrics": copy.deepcopy(metrics),
            "dimensions": copy.deepcopy(dimensions),
            "filters": copy.deepcopy(filters),
            "having": copy.deepcopy(having),
        },
    }


def _is_metric_filter_mention(mention: dict[str, Any], metrics: list[dict[str, Any]]) -> bool:
    marker = str(
        mention.get("target")
        or mention.get("slot_type")
        or mention.get("type")
        or mention.get("filter_type")
        or ""
    ).strip().lower()
    if marker in {"metric", "metric_filter", "having"}:
        return True
    if str(mention.get("asset_type") or "").upper() == "METRIC":
        return True
    name = str(mention.get("name") or mention.get("metric") or "").strip()
    return bool(name and _metric_for_having({"name": name}, metrics) is not None)


def _metric_for_having(mention: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any] | None:
    asset_id = int_or_none(mention.get("asset_id") or mention.get("metric_id"))
    if asset_id is not None:
        for metric in metrics:
            if int_or_none(metric.get("asset_id")) == asset_id:
                return metric
    name = str(mention.get("name") or mention.get("metric") or "").strip()
    if name:
        for metric in metrics:
            names = {
                str(metric.get("display_name") or "").strip(),
                str(metric.get("name") or "").strip(),
                str(metric.get("biz_name") or "").strip(),
            }
            if name in names:
                return metric
    if len(metrics) == 1 and _is_explicit_metric_marker(mention):
        return metrics[0]
    return None


def _is_explicit_metric_marker(mention: dict[str, Any]) -> bool:
    marker = str(
        mention.get("target")
        or mention.get("slot_type")
        or mention.get("type")
        or mention.get("filter_type")
        or ""
    ).strip().lower()
    return marker in {"metric", "metric_filter", "having"} or str(mention.get("asset_type") or "").upper() == "METRIC"


def _safe_having_operator(operator: Any) -> str:
    normalized = str(operator or "=").strip().lower()
    return normalized if normalized in {"=", "!=", "<>", ">", "<", ">=", "<="} else "="


def derive_value_filter_slots(
    knowledge: dict[str, Any],
    existing_filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把命中的 VALUE 资产翻译为"父维度 = 标准值"的过滤条件（B9）。

    Semantic 的 VALUE 元素复用父维度的 id，matched_text 是用户问题里命中的
    维值文本；schema_value_maps 用于把别名归一为标准存储值。
    """

    selected_assets = knowledge.get("selected_assets") if isinstance(knowledge.get("selected_assets"), dict) else {}
    existing_keys = {
        (int_or_none(item.get("asset_id")), str(item.get("value")))
        for item in existing_filters
        if str(item.get("asset_type") or "").upper() in {"", "DIMENSION"}
    }
    slots: list[dict[str, Any]] = []
    for item in slot_items(selected_assets.get("values")):
        dimension_id = int_or_none(item.get("asset_id"))
        matched_text = str(item.get("matched_text") or "").strip()
        if dimension_id is None or not matched_text:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        value = _canonical_dimension_value(matched_text, payload.get("schema_value_maps"))
        if (dimension_id, str(value)) in existing_keys:
            continue
        existing_keys.add((dimension_id, str(value)))
        slots.append(
            {
                "asset_type": "DIMENSION",
                "asset_id": dimension_id,
                "display_name": item.get("display_name") or item.get("name") or item.get("biz_name"),
                "operator": "=",
                "value": value,
            }
        )
    return slots


def _canonical_dimension_value(matched_text: str, schema_value_maps: Any) -> str:
    """用维值映射把命中的别名归一为标准值；无映射时保留原文。"""

    if not isinstance(schema_value_maps, list):
        return matched_text
    for value_map in schema_value_maps:
        if not isinstance(value_map, dict):
            continue
        canonical = str(value_map.get("value") or value_map.get("bizName") or "").strip()
        if not canonical:
            continue
        if matched_text == canonical:
            return canonical
        aliases = value_map.get("alias") or value_map.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if any(matched_text == str(alias).strip() for alias in aliases if alias):
            return canonical
    return matched_text


class QueryPlanBinder:
    """把知识检索证据与意图绑定为语义查询计划。"""

    def bind(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        knowledge = ctx.knowledge
        intent = ctx.intent

        if not knowledge.get("hit"):
            return self._infeasible("knowledge_missed", "知识检索未命中可用资产")

        # 知识层已判定维度与指标模型不兼容：前移阻断，避免编译期 JOIN 硬失败。
        decision = knowledge.get("decision") if isinstance(knowledge.get("decision"), dict) else {}
        if decision.get("reason_code") == "DIMENSION_NOT_IN_METRIC_MODEL" or decision.get("status") == "infeasible":
            reason_payload = decision.get("infeasible_reason")
            reason_code = "DIMENSION_NOT_IN_METRIC_MODEL"
            if isinstance(reason_payload, dict):
                reason_code = str(reason_payload.get("code") or reason_code)
                reason_text = str(reason_payload.get("reason") or decision.get("reason") or "维度与指标不兼容")
                suggestions = list(reason_payload.get("suggestions") or decision.get("suggestions") or [])
            else:
                reason_text = str(decision.get("reason") or "维度与指标不兼容")
                suggestions = list(decision.get("suggestions") or [])
            issues = [{"type": reason_code, "reason": reason_text}]
            for suggestion in suggestions:
                issues.append({"type": "suggestion", "reason": str(suggestion)})
            return QueryPlanOutput(
                status="infeasible",
                strategy="infeasible",
                infeasible_reason=reason_code,
                issues=issues,
            ).model_dump(mode="json")

        slots = derive_semantic_slots(knowledge, intent)
        selected_metric = selected_metric_from_response(knowledge, ctx.metric_selection)
        if selected_metric is not None:
            slots["metrics"] = [_selected_metric_slot(selected_metric)]
            selected_assets = knowledge.get("selected_assets") if isinstance(knowledge.get("selected_assets"), dict) else {}
            selected_assets = {**selected_assets, "metrics": [selected_metric]}
            slots["dimensions"], slots["filters"] = prune_dimensions_for_selected_metric(
                selected_assets,
                slots,
                intent,
            )
        order, limit = derive_order_and_limit(intent, slots)
        filters = [*slots["filters"], *derive_value_filter_slots(knowledge, slots["filters"])]
        metrics = slots["metrics"]
        group_bys = slots["dimensions"]
        having = derive_having(intent, metrics)
        if not metrics and not group_bys and not filters:
            return self._infeasible("no_bindable_assets", "没有可绑定到查询计划的资产")
        sub_plans = derive_multi_query_sub_plans(intent, metrics, group_bys, filters, having)
        capability = decide_capability(intent, {"sub_plans": sub_plans})
        if capability.status == "infeasible":
            return self._infeasible(
                capability.reason_code or "query_plan_infeasible",
                capability.reason or "查询计划不可行",
            )

        # 多查询要素不全被回退为单查询时，携带说明供回答侧如实提示（不静默降级）。
        issues: list[dict[str, Any]] = []
        effective_sub_plans = sub_plans
        if capability.strategy != "multi_query":
            effective_sub_plans = []
            if capability.downgrade_note:
                issues.append(
                    {
                        "type": capability.reason_code or "multi_query_downgraded_to_single",
                        "reason": capability.downgrade_note,
                    }
                )

        return QueryPlanOutput(
            status=capability.status,
            strategy=capability.strategy,
            select_mode=derive_select_mode(intent),
            metrics=metrics,
            group_bys=group_bys,
            filters=filters,
            having=having,
            time=derive_time_bucket(knowledge, intent),
            order=order,
            limit=limit,
            sub_plans=effective_sub_plans,
            issues=issues,
        ).model_dump(mode="json")

    @staticmethod
    def _infeasible(reason_code: str, reason: str) -> dict[str, Any]:
        return QueryPlanOutput(
            status="infeasible",
            strategy="infeasible",
            infeasible_reason=reason_code,
            issues=[{"type": reason_code, "reason": reason}],
        ).model_dump(mode="json")


def _selected_metric_slot(metric: dict[str, Any]) -> dict[str, Any]:
    slot = {
        "asset_type": "METRIC",
        "asset_id": metric.get("asset_id"),
        "display_name": metric.get("display_name") or metric.get("name") or metric.get("biz_name"),
        "operator": None,
        "value": None,
    }
    if metric.get("model_id") is not None:
        slot["model_id"] = metric["model_id"]
    return slot
