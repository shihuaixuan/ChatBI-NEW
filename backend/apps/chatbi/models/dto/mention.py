"""R1 提及契约：带原文跨度的语义提及与分析表达。"""

from __future__ import annotations

import importlib
import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MentionKind = Literal[
    "metric_phrase",
    "dimension_phrase",
    "filter_value",
    "time_expression",
]
MetricRole = Literal["base", "computed", "composite_unknown"]
DimensionRole = Literal["group_by", "filter", "display", "unresolved"]

_COMPUTED_MARKERS = (
    "增长率",
    "增长幅度",
    "同比",
    "环比",
    "占比",
    "比例",
    "贡献率",
    "差值",
    "变化率",
    "变化幅度",
)
_COMPOSITE_SUFFIXES = ("平均客单价", "客单价")

MentionIntentType = Literal[
    "metric_query",
    "trend_analysis",
    "ranking_analysis",
    "comparison_analysis",
    "detail_query",
    "share_analysis",
    "composition",
    "multi_step",
    "anomaly_analysis",
    "unknown",
]
RequiredSlotType = Literal[
    "metric",
    "dimension",
    "time_dimension",
    "time_range",
    "filter",
    "order",
    "limit",
    "comparison_target",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecompositionHint(_StrictModel):
    """复合指标的拆解假设；假设文本不是用户提及，不携带跨度。"""

    kind: Literal["ratio"]
    numerator_text: str = Field(min_length=1)
    denominator_text: str = Field(min_length=1)


class SemanticMention(_StrictModel):
    """用户原文中的一个连续语义短语。"""

    mention_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    kind: MentionKind
    metric_role: MetricRole | None = None
    decomposition: DecompositionHint | None = None
    dimension_role: DimensionRole | None = None
    attached_to: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> SemanticMention:
        """保证 mention 的字段只服务于所属语义类型。"""

        if self.kind == "metric_phrase":
            if self.metric_role is None:
                raise ValueError("MENTION_METRIC_ROLE_REQUIRED")
            if self.dimension_role is not None or self.attached_to is not None:
                raise ValueError("MENTION_METRIC_FIELDS_CONFLICT")
            if self.metric_role != "composite_unknown" and self.decomposition is not None:
                raise ValueError("MENTION_DECOMPOSITION_ROLE_CONFLICT")
        elif self.kind == "dimension_phrase":
            if self.dimension_role is None:
                raise ValueError("MENTION_DIMENSION_ROLE_REQUIRED")
            if self.metric_role is not None or self.decomposition is not None:
                raise ValueError("MENTION_DIMENSION_FIELDS_CONFLICT")
        elif self.kind == "filter_value":
            if self.metric_role is not None or self.dimension_role is not None:
                raise ValueError("MENTION_FILTER_FIELDS_CONFLICT")
        elif self.metric_role is not None or self.dimension_role is not None:
            raise ValueError("MENTION_TIME_FIELDS_CONFLICT")
        return self


class AnalysisExpression(_StrictModel):
    """用户要求的计算或比较表达，与基础指标提及分离。"""

    expr_id: str = Field(min_length=1)
    op: Literal["growth", "share", "ratio", "diff", "compare", "topn"]
    display_name: str = Field(min_length=1)
    of: list[str] = Field(default_factory=list)
    over: Literal["time_comparison", "dimension_total", "explicit"] | None = None
    outputs: list[Literal["difference", "rate", "share", "value"]] = Field(
        default_factory=list
    )


class MetricCondition(_StrictModel):
    """指标阈值条件；WHERE/HAVING 由服务端确定性裁决。"""

    metric_ref: str = Field(min_length=1)
    operator: Literal[">", ">=", "<", "<=", "=", "!="]
    value: float | int
    raw: str = Field(min_length=1)


class OrderRef(_StrictModel):
    """对已有指标或表达排序，不创建重复指标提及。"""

    ref: str = Field(min_length=1)
    direction: Literal["asc", "desc"]
    selection: Literal["all", "top_n", "bottom_n"] = "all"
    limit: int | None = Field(default=None, ge=1, le=1000)


class MentionGraph(_StrictModel):
    """一次问题理解的提及、表达、条件和排序全集。"""

    mentions: list[SemanticMention] = Field(default_factory=list)
    expressions: list[AnalysisExpression] = Field(default_factory=list)
    metric_conditions: list[MetricCondition] = Field(default_factory=list)
    order: OrderRef | None = None
    intent_type: MentionIntentType = "unknown"
    query_shape: dict[str, Any] = Field(
        default_factory=lambda: {"select_mode": "aggregate"}
    )
    unresolved_notes: list[str] = Field(default_factory=list)
    category: Literal["chitchat", "data_query", "meta_query", "out_of_scope"] = (
        "data_query"
    )
    confidence: float = Field(default=0.0, ge=0, le=1)
    required_slot_types: list[RequiredSlotType] = Field(default_factory=list)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)


def _normalize_expression(
    item: dict[str, Any],
    *,
    rewritten_question: str,
    intent_type: str,
) -> dict[str, Any]:
    """把模型省略的表达字段补齐为稳定的 R1 结构。"""

    raw_op = str(item.get("op") or "").strip().lower()
    if raw_op in {"comparison", "comparison_analysis"}:
        raw_op = "compare"
    if raw_op == "compare" and any(
        marker in rewritten_question for marker in ("增长率", "环比", "同比", "变化")
    ):
        raw_op = "growth"
    # 模型有时把“占比/比例”输出为 ratio。R1 中 ratio 只表示原文明确给出
    # 分子和分母的显式比率；普通“某维度占比”统一归一为 share。
    if raw_op == "ratio" and any(
        marker in rewritten_question for marker in ("占比", "比例", "份额", "贡献率")
    ):
        raw_op = "share"
    if raw_op not in {"growth", "share", "ratio", "diff", "compare", "topn"}:
        if any(marker in rewritten_question for marker in ("增长率", "环比", "同比", "变化")):
            raw_op = "growth"
        elif any(marker in rewritten_question for marker in ("占比", "比例", "份额")):
            raw_op = "share"
        elif intent_type == "comparison_analysis":
            raw_op = "compare"
        else:
            raw_op = "ratio" if "平均" in rewritten_question else "diff"
    display_name = str(item.get("display_name") or "").strip()
    if not display_name:
        display_name = {
            "growth": "增长率",
            "share": "占比",
            "ratio": "比率",
            "diff": "差值",
            "compare": "对比",
            "topn": "排名",
        }[raw_op]
    outputs = item.get("outputs")
    valid_outputs = {"difference", "rate", "share", "value"}
    if isinstance(outputs, list):
        outputs = [
            str(value)
            for value in outputs
            if str(value) in valid_outputs
        ]
    else:
        outputs = []
    if not outputs:
        outputs = {
            "growth": ["difference", "rate"],
            "share": ["share"],
            "ratio": ["value"],
            "diff": ["difference"],
            "compare": ["value"],
            "topn": ["value"],
        }[raw_op]
    over = item.get("over")
    if isinstance(over, list):
        over = "time_comparison" if raw_op in {"growth", "compare"} else "explicit"
    if over not in {"time_comparison", "dimension_total", "explicit", None}:
        over = "explicit"
    item.update(
        {
            "op": raw_op,
            "display_name": display_name,
            "outputs": outputs,
            "over": over,
        }
    )
    return item


def _repair_metric_phrase(item: dict[str, Any]) -> None:
    """修复模型把计算后缀并入指标、或把通用“指标”后缀保留的问题。"""

    text = str(item.get("text") or "").strip()
    if not text or item.get("kind") != "metric_phrase":
        return
    if text.endswith("指标") and len(text) > 2:
        item["text"] = text[:-2]
        text = item["text"]
    for suffix in _COMPOSITE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            prefix = text[: -len(suffix)]
            item["metric_role"] = "composite_unknown"
            item["decomposition"] = {
                "kind": "ratio",
                "numerator_text": f"{prefix}金额",
                "denominator_text": f"{prefix}数",
            }
            return
    for suffix in _COMPUTED_MARKERS:
        if text.endswith(suffix) and len(text) > len(suffix):
            item["text"] = text[: -len(suffix)]
            item["metric_role"] = "base"
            item.pop("decomposition", None)
            return


def _normalize_query_shape(value: Any, notes: list[str]) -> dict[str, Any]:
    """把查询形态收敛到执行 DTO 支持的字段和值。"""

    shape = dict(value) if isinstance(value, dict) else {}
    shape.setdefault("select_mode", "aggregate")
    aliases = {
        "日": "day",
        "天": "day",
        "每日": "day",
        "周": "week",
        "每周": "week",
        "月": "month",
        "每月": "month",
        "季度": "quarter",
        "季": "quarter",
        "年": "year",
        "每年": "year",
    }
    raw_grain = shape.get("time_grain")
    if raw_grain is not None:
        grain = str(raw_grain).strip().lower()
        grain = aliases.get(grain, grain)
        if grain not in {"day", "week", "month", "quarter", "year"}:
            # “上半年/半年”是时间范围，不是按时间桶执行的粒度；比较时段
            # 仍由 time_expression 和 temporal 域保留，不能把它塞进 QueryShape。
            notes.append(f"query_shape.time_grain_unusable:{raw_grain}")
            shape["time_grain"] = None
        else:
            shape["time_grain"] = grain
    return shape

def normalize_mention_graph_payload(
    payload: dict[str, Any],
    *,
    rewritten_question: str,
) -> dict[str, Any]:
    """在 Pydantic 校验前修复跨度、去重并清理无效引用。"""

    normalized = deepcopy(payload)
    notes = [
        str(item).strip()
        for item in normalized.get("unresolved_notes") or []
        if str(item).strip()
    ]
    normalized["query_shape"] = _normalize_query_shape(
        normalized.get("query_shape"), notes
    )
    raw_mentions = normalized.get("mentions")
    mentions: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    metric_aliases: dict[tuple[str, str], str] = {}
    occupied: list[tuple[int, int, str]] = []
    raw_items = raw_mentions if isinstance(raw_mentions, list) else []
    duplicate_metric_spans: dict[int, tuple[int, int]] = {}
    duplicate_metric_texts: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict) or str(raw.get("kind") or "") != "metric_phrase":
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        same_text_indexes = [
            candidate_index
            for candidate_index, candidate in enumerate(raw_items)
            if isinstance(candidate, dict)
            and str(candidate.get("kind") or "") == "metric_phrase"
            and str(candidate.get("text") or "").strip() == text
        ]
        matches = _find_all(rewritten_question, text)
        if len(same_text_indexes) == len(matches) and len(matches) > 1:
            duplicate_metric_texts.add(text.casefold())
            duplicate_metric_spans.update(
                zip(sorted(same_text_indexes), matches, strict=True)
            )
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            notes.append(f"mention[{index}]:invalid")
            continue
        item = dict(raw)
        mention_id = str(item.get("mention_id") or f"m{index + 1}").strip()
        text = str(item.get("text") or "").strip()
        if not text:
            notes.append(f"mention[{index}]:empty_text")
            continue
        _repair_metric_phrase(item)
        text = str(item.get("text") or "").strip()
        start = item.get("start_offset")
        end = item.get("end_offset")
        if not _slice_matches(rewritten_question, text, start, end):
            matches = _find_all(rewritten_question, text)
            assigned_span = duplicate_metric_spans.get(index)
            if assigned_span is not None:
                start, end = assigned_span
            elif len(matches) != 1:
                notes.append(f"{mention_id}:span_unresolved")
                continue
            else:
                start, end = matches[0]
        kind = str(item.get("kind") or "").strip()
        if kind not in {"metric_phrase", "dimension_phrase", "filter_value", "time_expression"}:
            notes.append(f"{mention_id}:kind_unresolved")
            continue
        if mention_id in aliases:
            aliases[mention_id] = aliases[mention_id]
            mention_id = f"m{index + 1}"
        overlap = next(
            (
                previous
                for previous in occupied
                if not (int(end) <= previous[0] or int(start) >= previous[1])
            ),
            None,
        )
        attached_to = item.get("attached_to")
        if overlap is not None and not (
            kind == "filter_value" and str(attached_to or "") == overlap[2]
        ):
            aliases[mention_id] = overlap[2]
            notes.append(f"{mention_id}:overlap_deduplicated")
            continue
        if kind == "metric_phrase":
            metric_role = str(item.get("metric_role") or "").strip()
            metric_key = (text.casefold(), metric_role)
            canonical_id = metric_aliases.get(metric_key)
            if canonical_id is not None and text.casefold() not in duplicate_metric_texts:
                # 同一问题中同一指标短语可出现多次（例如“各店铺总GMV”与
                # “全部店铺总GMV”）。资产检索只需要一个指标槽，表达式引用
                # 通过 alias 归并到同一 mention，范围差异由维度角色表达。
                aliases[str(raw.get("mention_id") or mention_id)] = canonical_id
                notes.append(f"{mention_id}:duplicate_metric_deduplicated")
                continue
        item.update(
            {
                "mention_id": mention_id,
                "text": text,
                "start_offset": int(start),
                "end_offset": int(end),
            }
        )
        if kind == "metric_phrase" and not item.get("metric_role"):
            item["metric_role"] = _fallback_metric_role(text)
        if kind == "dimension_phrase" and not item.get("dimension_role"):
            item["dimension_role"] = "unresolved"
        if item.get("metric_role") != "composite_unknown":
            item.pop("decomposition", None)
        else:
            item["decomposition"] = _normalize_decomposition(
                item.get("decomposition"), notes, mention_id
            )
        occupied.append((int(start), int(end), mention_id))
        aliases.setdefault(str(raw.get("mention_id") or mention_id), mention_id)
        if kind == "metric_phrase":
            metric_role = str(item.get("metric_role") or "").strip()
            metric_aliases.setdefault((text.casefold(), metric_role), mention_id)
        mentions.append(item)

    mentions.sort(key=lambda item: (item["start_offset"], item["end_offset"], item["mention_id"]))
    mention_ids = {item["mention_id"] for item in mentions}
    expressions: list[dict[str, Any]] = []
    expression_ids: set[str] = set()
    for raw in normalized.get("expressions") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_expression(
            dict(raw),
            rewritten_question=rewritten_question,
            intent_type=str(normalized.get("intent_type") or "unknown"),
        )
        expr_id = str(item.get("expr_id") or "").strip()
        refs = [_resolve_ref(str(ref), aliases) for ref in item.get("of") or []]
        metric_ids = {
            mention["mention_id"]
            for mention in mentions
            if mention.get("kind") == "metric_phrase"
        }
        if refs and any(ref not in metric_ids and ref not in expression_ids for ref in refs):
            # 模型偶尔把时间/维度 mention_id 填入表达式引用；表达式只能绑定
            # 指标或已存在表达式，确定性回退到本问题的指标提及集合。
            refs = [
                mention["mention_id"]
                for mention in mentions
                if mention.get("kind") == "metric_phrase"
            ]
        if not expr_id or expr_id in expression_ids or not refs or not all(
            ref in mention_ids or ref in expression_ids for ref in refs
        ):
            notes.append(f"{expr_id or 'expression'}:reference_unresolved")
            continue
        item["expr_id"] = expr_id
        item["of"] = list(dict.fromkeys(refs))
        expression_ids.add(expr_id)
        expressions.append(item)

    # 比较问题可能只输出 comparison_analysis，没有显式表达对象；这里把
    # 已抽取的基础指标和时间比较组合成一个确定性的 compare 表达。
    if (
        not expressions
        and str(normalized.get("intent_type") or "") == "comparison_analysis"
    ):
        metric_ids = [
            item["mention_id"]
            for item in mentions
            if item.get("kind") == "metric_phrase"
            and item.get("metric_role") != "computed"
        ]
        if metric_ids:
            expressions.append(
                {
                    "expr_id": "e1",
                    "op": "compare",
                    "display_name": "对比",
                    "of": metric_ids,
                    "over": "time_comparison",
                    "outputs": ["value"],
                }
            )
            expression_ids.add("e1")

    if not expressions:
        metric_ids = [
            item["mention_id"]
            for item in mentions
            if item.get("kind") == "metric_phrase"
            and item.get("metric_role") != "computed"
        ]
        inferred_op: str | None = None
        if any(marker in rewritten_question for marker in ("增长率", "环比", "同比", "变化")):
            inferred_op = "growth"
        elif any(marker in rewritten_question for marker in ("占比", "比例", "份额")):
            inferred_op = "share"
        if metric_ids and inferred_op is not None:
            expressions.append(
                {
                    "expr_id": "e1",
                    "op": inferred_op,
                    "display_name": "增长率" if inferred_op == "growth" else "占比",
                    "of": metric_ids,
                    "over": "time_comparison" if inferred_op == "growth" else "dimension_total",
                    "outputs": ["difference", "rate"] if inferred_op == "growth" else ["share"],
                }
            )
            expression_ids.add("e1")

    referenced = {ref for expression in expressions for ref in expression["of"]}
    for mention in mentions:
        if mention.get("metric_role") == "computed" and mention["mention_id"] not in referenced:
            mention["metric_role"] = "composite_unknown"
            mention.pop("decomposition", None)
            notes.append(f"{mention['mention_id']}:computed_without_expression")

    valid_refs = mention_ids | expression_ids
    conditions = []
    for raw in normalized.get("metric_conditions") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["metric_ref"] = _resolve_ref(str(item.get("metric_ref") or ""), aliases)
        if item["metric_ref"] in valid_refs:
            conditions.append(item)
        else:
            notes.append("metric_condition:reference_unresolved")
    order = normalized.get("order")
    if isinstance(order, dict):
        order = dict(order)
        order["ref"] = _resolve_ref(str(order.get("ref") or ""), aliases)
        if order["ref"] not in valid_refs:
            notes.append("order:reference_unresolved")
            order = None
    if order is None and (normalized.get("query_shape") or {}).get("needs_order_by"):
        metric = next(
            (
                item
                for item in mentions
                if item.get("kind") == "metric_phrase"
                and item.get("metric_role") != "computed"
            ),
            None,
        )
        if metric is not None:
            query_shape = normalized.get("query_shape") or {}
            direction = str(query_shape.get("order_direction") or "").lower()
            if direction not in {"asc", "desc"}:
                direction = "desc" if "降序" in rewritten_question else "asc"
            limit = query_shape.get("limit")
            order = {
                "ref": metric["mention_id"],
                "direction": direction,
                "selection": "top_n" if isinstance(limit, int) else "all",
                "limit": limit if isinstance(limit, int) else None,
            }

    normalized_slots = []
    slot_aliases = {
        "metrics": "metric",
        "measure": "metric",
        "measures": "metric",
        "dimensions": "dimension",
        "time": "time_dimension",
        "time_filter": "time_range",
        "filter_value": "filter",
        "comparison_baseline": "comparison_target",
        "comparison_period": "comparison_target",
    }
    for raw_slot in normalized.get("required_slot_types") or []:
        slot = slot_aliases.get(str(raw_slot).strip(), str(raw_slot).strip())
        if slot in {
            "metric",
            "dimension",
            "time_dimension",
            "time_range",
            "filter",
            "order",
            "limit",
            "comparison_target",
        }:
            normalized_slots.append(slot)

    normalized.update(
        {
            "mentions": mentions,
            "expressions": expressions,
            "metric_conditions": conditions,
            "order": order,
            "unresolved_notes": list(dict.fromkeys(notes)),
            "required_slot_types": list(dict.fromkeys(normalized_slots)),
            "ambiguous_slots": list(dict.fromkeys(str(item) for item in normalized.get("ambiguous_slots") or [])),
            "conflict_slots": list(dict.fromkeys(str(item) for item in normalized.get("conflict_slots") or [])),
        }
    )
    _recover_expression_metrics(normalized, rewritten_question)
    _normalize_query_intent(normalized, rewritten_question)
    return MentionGraph.model_validate(normalized).model_dump(mode="json")


def _recover_expression_metrics(
    payload: dict[str, Any],
    rewritten_question: str,
) -> None:
    """当模型只登记“比例/增长率”等结果词时恢复其前置基础指标。

    计算词属于表达，不是可检索指标。恢复规则只使用同一问题中已经登记的
    时间、维度和筛选提及，去除这些结构后保留表达式左侧的连续文本；不读取
    语义资产，也不把推测出的名称写入用户原文之外。
    """

    mentions = payload.get("mentions")
    expressions = payload.get("expressions")
    if not isinstance(mentions, list) or not isinstance(expressions, list):
        return
    has_base_metric = any(
        isinstance(item, dict)
        and item.get("kind") == "metric_phrase"
        and item.get("metric_role") != "computed"
        for item in mentions
    )
    if has_base_metric or not any(
        isinstance(item, dict) and item.get("op") == "share"
        for item in expressions
    ):
        return
    separator = next(
        (token for token in ("占比", "占全部", "占") if token in rewritten_question),
        None,
    )
    if separator is None:
        return
    prefix = rewritten_question.split(separator, 1)[0]
    removable = [
        str(item.get("text") or "")
        for item in mentions
        if isinstance(item, dict)
        and item.get("kind") in {"time_expression", "dimension_phrase", "filter_value"}
    ]
    for text in sorted((item for item in removable if item), key=len, reverse=True):
        prefix = prefix.replace(text, "")
    prefix = re.sub(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?", "", prefix)
    prefix = re.sub(r"^[各每个每一所有全部全量]+", "", prefix)
    metric_text = re.sub(r"^[\s、，,的]+|[\s、，,的]+$", "", prefix)
    if not metric_text:
        return
    start = rewritten_question.find(metric_text)
    if start < 0:
        return
    mention_id = _next_mention_id(mentions)
    mentions.append(
        {
            "mention_id": mention_id,
            "text": metric_text,
            "start_offset": start,
            "end_offset": start + len(metric_text),
            "kind": "metric_phrase",
            "metric_role": "base",
        }
    )
    for expression in expressions:
        if isinstance(expression, dict) and expression.get("op") == "share":
            expression.setdefault("of", []).append(mention_id)


def _next_mention_id(mentions: list[Any]) -> str:
    existing = {
        str(item.get("mention_id"))
        for item in mentions
        if isinstance(item, dict)
    }
    index = 1
    while f"m{index}" in existing:
        index += 1
    return f"m{index}"


def _normalize_query_intent(payload: dict[str, Any], rewritten_question: str) -> None:
    """把查询形态中可由原文确定的结构归一到执行契约。"""

    shape = payload.get("query_shape")
    if not isinstance(shape, dict):
        shape = {}
        payload["query_shape"] = shape
    conditions = payload.get("metric_conditions")
    if isinstance(conditions, list) and conditions:
        # 指标条件作用于聚合值，不能把模型误报的明细模式带入计划。
        shape["select_mode"] = "aggregate"
        if str(payload.get("intent_type") or "") == "detail_query":
            payload["intent_type"] = "metric_query"

    metric_mentions = [
        item
        for item in payload.get("mentions") or []
        if isinstance(item, dict)
        and item.get("kind") == "metric_phrase"
        and item.get("metric_role") != "computed"
    ]
    intent_type = str(payload.get("intent_type") or "unknown")
    if intent_type == "multi_step" and len(metric_mentions) > 1 and not any(
        marker in rewritten_question for marker in ("下钻", "归因", "先", "再")
    ):
        payload["intent_type"] = "metric_query"

    order = payload.get("order")
    if order is None and metric_mentions and any(
        marker in rewritten_question for marker in ("降序", "升序", "最高", "最低", "最多", "最少", "前")
    ):
        direction = "asc" if any(marker in rewritten_question for marker in ("升序", "最低", "最少")) else "desc"
        payload["order"] = {
            "ref": metric_mentions[0].get("mention_id"),
            "direction": direction,
            "selection": "all",
            "limit": None,
        }
        shape["needs_order_by"] = True
        shape["order_direction"] = direction
    if payload.get("order") is not None and payload.get("intent_type") in {
        "metric_query",
        "unknown",
    }:
        payload["intent_type"] = "ranking_analysis"


def __getattr__(name: str) -> Any:
    """兼容旧导入路径，同时避免 DTO 模块形成循环依赖。"""

    if name == "project_mention_graph_to_intent":
        module = importlib.import_module(
            "apps.chatbi.models.dto.intent_projection"
        )
        return module.project_mention_graph_to_intent
    raise AttributeError(name)


def _normalize_decomposition(
    value: Any,
    notes: list[str],
    mention_id: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    numerator = str(value.get("numerator_text") or "").strip()
    denominator = str(value.get("denominator_text") or "").strip()
    if not numerator or not denominator or numerator.casefold() == denominator.casefold():
        notes.append(f"{mention_id}:decomposition_invalid")
        return None
    return {
        "kind": "ratio",
        "numerator_text": numerator,
        "denominator_text": denominator,
    }


def _fallback_metric_role(text: str) -> str:
    return "computed" if any(marker in text for marker in _COMPUTED_MARKERS) else "base"


def _resolve_ref(value: str, aliases: dict[str, str]) -> str:
    return aliases.get(value, value)


def _slice_matches(question: str, text: str, start: Any, end: Any) -> bool:
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(question)
        and question[start:end] == text
    )


def _find_all(question: str, text: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    offset = 0
    while True:
        index = question.find(text, offset)
        if index < 0:
            return result
        result.append((index, index + len(text)))
        offset = index + max(len(text), 1)


__all__ = [
    "AnalysisExpression",
    "DimensionRole",
    "DecompositionHint",
    "MentionKind",
    "MentionGraph",
    "MentionIntentType",
    "MetricRole",
    "MetricCondition",
    "OrderRef",
    "RequiredSlotType",
    "SemanticMention",
    "normalize_mention_graph_payload",
]
