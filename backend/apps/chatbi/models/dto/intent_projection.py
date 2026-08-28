"""MentionGraph 到旧 IntentRecognitionOutput 的兼容投影。"""

from __future__ import annotations

import re
from typing import Any

from apps.chatbi.models.dto.mention import MentionGraph
from apps.chatbi.models.dto.question_understanding import (
    DimensionSlot,
    IntentRecognitionOutput,
    QueryShape,
    RankingSpec,
    TimeRange,
)


def project_mention_graph_to_intent(
    graph: MentionGraph,
    *,
    rewritten_question: str = "",  # noqa: ARG001 保留兼容调用参数
) -> IntentRecognitionOutput:
    """把 MentionGraph 投影为执行层使用的自然语言意图。

    MentionGraph 是理解层事实源，投影只补齐旧执行 DTO 仍然需要的字段，
    不重新判断指标、维度或表达含义。
    """

    dimensions = [item for item in graph.mentions if item.kind == "dimension_phrase"]
    values_by_dimension: dict[str, list[Any]] = {}
    for item in graph.mentions:
        if item.kind != "filter_value" or not item.attached_to:
            continue
        values_by_dimension.setdefault(item.attached_to, []).append(item.text)
    dimension_slots = []
    for item in dimensions:
        values = values_by_dimension.get(item.mention_id, [])
        role = item.dimension_role or ("filter" if values else "unresolved")
        dimension_name = _dimension_name_for_execution(item.text)
        if role == "filter" and not values:
            suffix_match = re.match(r"^(.*?)([A-Za-z]?\d{3,})$", dimension_name)
            if suffix_match:
                dimension_name = suffix_match.group(1).strip()
                values = [suffix_match.group(2)]
        # “全部店铺/所有渠道”表达的是占比或比较的总集合，不是缺失值的
        # 用户筛选条件；该语义由表达式 over 负责，不能生成澄清卡片。
        if role == "filter" and not values and _is_total_scope(item.text):
            continue
        value: Any = values[0] if len(values) == 1 else values or None
        dimension_slots.append(
            DimensionSlot(
                name=dimension_name,
                role="ambiguous" if role == "unresolved" else role,
                value=value,
                value_status="provided" if values else "not_provided",
                value_confidence=1.0 if values else 0.0,
            )
        )
    metric_mentions = list(
        dict.fromkeys(
            item.text
            for item in graph.mentions
            if item.kind == "metric_phrase" and item.metric_role != "computed"
        )
    )
    ranking = None
    projected_intent_type = graph.intent_type
    if graph.order is not None:
        ordered_mention = next(
            (item.text for item in graph.mentions if item.mention_id == graph.order.ref),
            None,
        )
        ordered_kind = next(
            (item.kind for item in graph.mentions if item.mention_id == graph.order.ref),
            None,
        )
        group_target = next(
            (
                _dimension_name_for_execution(item.text)
                for item in graph.mentions
                if item.kind == "dimension_phrase"
                and item.dimension_role == "group_by"
            ),
            None,
        )
        target = (
            group_target or ordered_mention
            if ordered_kind == "metric_phrase"
            else ordered_mention
        )
        if target:
            ranking = RankingSpec(
                target=target,
                metric=metric_mentions[0] if metric_mentions else None,
                direction=graph.order.direction,
                selection=graph.order.selection,
                limit=graph.order.limit,
            )
    query_shape_payload = dict(graph.query_shape)
    if (
        ranking is not None
        and query_shape_payload.get("select_mode") == "aggregate"
        and query_shape_payload.get("needs_group_by") is True
    ):
        # 聚合结果按指标排序属于排名分析；明细排序仍保留 detail_query。
        projected_intent_type = "ranking_analysis"
    if graph.metric_conditions:
        # 条件的执行阶段由绑定后的资产契约决定；这里仅保留原始条件，
        # 由统一编译计划把它放到 HAVING，而不是让模型直接指定 WHERE/HAVING。
        query_shape_payload["having"] = [
            {
                "metric_ref": item.metric_ref,
                "operator": item.operator,
                "value": item.value,
                "raw": item.raw,
            }
            for item in graph.metric_conditions
        ]
    if graph.order is not None:
        query_shape_payload.setdefault("needs_order_by", True)
        query_shape_payload.setdefault("order_direction", graph.order.direction)
        if graph.order.limit is not None:
            query_shape_payload.setdefault("limit", graph.order.limit)
    query_shape = QueryShape.model_validate(query_shape_payload)
    metric_name_by_id = {
        item.mention_id: item.text
        for item in graph.mentions
        if item.kind == "metric_phrase"
    }
    filter_mentions = [
        {
            "name": metric_name_by_id.get(item.metric_ref, item.metric_ref),
            "metric_ref": item.metric_ref,
            "operator": item.operator,
            "value": item.value,
            "raw": item.raw,
            "asset_type": "METRIC",
        }
        for item in graph.metric_conditions
    ]
    return IntentRecognitionOutput(
        category=graph.category,
        intent_type=projected_intent_type,
        confidence=graph.confidence,
        metric_mentions=metric_mentions,
        dimension_mentions=[
            _dimension_name_for_execution(item.text)
            for item in dimensions
            if not (item.dimension_role == "filter" and _is_total_scope(item.text))
        ],
        dimension_slots=dimension_slots,
        filter_mentions=filter_mentions,
        time_mentions=[
            item.text for item in graph.mentions if item.kind == "time_expression"
        ],
        time_range=TimeRange(),
        required_slot_types=list(graph.required_slot_types),
        query_shape=query_shape,
        ranking=ranking,
        ambiguous_slots=list(graph.ambiguous_slots),
        conflict_slots=list(graph.conflict_slots),
    )


def _dimension_name_for_execution(value: str) -> str:
    """去除维度短语中的分组量词，保留业务对象名称。"""

    text = value.strip()
    for prefix in ("每一个", "每个", "各个", "各"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :].strip()
    return text


def _is_total_scope(value: str) -> bool:
    text = value.strip()
    return text.startswith(("全部", "所有", "全量"))


__all__ = ["project_mention_graph_to_intent"]
