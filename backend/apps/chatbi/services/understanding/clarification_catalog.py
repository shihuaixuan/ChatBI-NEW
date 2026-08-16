"""理解校验 reason_code 的澄清/拒答归宿注册表（P0-1 澄清全覆盖）。

不变量：validation.status != valid 的每个 reason_code 都必须有归宿——
结构化澄清卡片（可恢复）或拒答建议（终端收口），绝不允许进入零工具死局。

卡片构建是确定性的：只从理解输出自身携带的槽位、候选与排名结构生成选项，
不发起模型或检索调用；无法可靠构造选项的 reason 落拒答档并附建议问法。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi.services.understanding.temporal_interpretation import (
    build_temporal_clarification_options,
)


class ClarificationCard(BaseModel):
    """问题理解阶段产生的确定性澄清请求。"""

    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    reason: str
    resume_payload: dict[str, Any] = Field(default_factory=dict)


class ClarificationRefusal(BaseModel):
    """无法构造可靠澄清时的拒答建议；由调用方以终端回答收口。"""

    answer: str
    reason_code: str


ClarificationOutcome = ClarificationCard | ClarificationRefusal


def evaluate_clarification(
    understanding: dict[str, Any],
) -> ClarificationOutcome | None:
    """按优先级为未通过校验的问题选择澄清卡片或拒答建议。

    返回 None 表示理解已通过校验（或缺少校验结构），可以继续主链路。
    """

    validation = understanding.get("validation")
    if not isinstance(validation, dict):
        return None
    if validation.get("status") != "clarification_required":
        return None
    reason_codes = {str(code) for code in validation.get("reason_codes") or []}

    for reason_code, builder in _CARD_BUILDERS.items():
        if reason_code not in reason_codes:
            continue
        outcome = builder(understanding)
        if outcome is not None:
            return outcome

    for reason_code in reason_codes:
        if reason_code in _REFUSAL_ANSWERS:
            answer, code = _REFUSAL_ANSWERS[reason_code](understanding)
            return ClarificationRefusal(answer=answer, reason_code=code)
    return ClarificationRefusal(
        answer=(
            "当前问题暂时无法转换为可执行的查询，请补充明确的指标、时间和筛选条件后重试，"
            "例如：“本月的总销售额是多少？”"
        ),
        reason_code=next(iter(reason_codes), "understanding_invalid"),
    )


# ---- 澄清卡片构建器（按处理优先级排序） ----


def _temporal_clarification(
    understanding: dict[str, Any],
) -> ClarificationCard | None:
    temporal_interpretation = understanding.get("temporal_interpretation")
    temporal_interpretation = (
        temporal_interpretation if isinstance(temporal_interpretation, dict) else {}
    )
    temporal_plan = temporal_interpretation.get("plan")
    temporal_plan = temporal_plan if isinstance(temporal_plan, dict) else {}
    ambiguities = [
        item
        for item in temporal_plan.get("ambiguities") or []
        if isinstance(item, dict)
    ]
    ambiguity_codes = {str(item.get("code") or "") for item in ambiguities}
    options = build_temporal_clarification_options(ambiguity_codes)
    question = "请提供明确的时间范围。"
    if "time_range_conflict" in ambiguity_codes:
        question = "问题中存在多个时间范围，请确认本次查询使用哪个时间范围。"
    elif "time_expression_unsupported" in ambiguity_codes:
        question = "当前时间表达暂不支持，请提供明确的起止日期。"
    return ClarificationCard(
        question=question,
        options=options,
        reason="时间计划尚未形成可执行的绝对范围，必须先确认后再检索语义资产。",
        resume_payload={"operation": "resolve_temporal_plan"},
    )


def _time_range_unsupported_card(
    _understanding: dict[str, Any],
) -> ClarificationCard | None:
    return ClarificationCard(
        question="当前时间表达暂不支持，请提供明确的起止日期（例如：2026年8月1日至8月15日）。",
        options=[],
        reason="时间表达无法解析为可执行范围，需要用户直接给定起止日期。",
        resume_payload={"operation": "resolve_time_range"},
    )


def _comparison_period_card(
    _understanding: dict[str, Any],
) -> ClarificationCard:
    """比较时段缺失时给出可恢复的固定选项。"""

    return ClarificationCard(
        question="请确认比较方式和对比时段。",
        options=[
            {"label": "同比", "value": "comparison:yoy"},
            {"label": "环比", "value": "comparison:mom"},
            {"label": "自定义对比期", "value": "comparison:custom"},
        ],
        reason="比较分析需要明确基期与对比期，避免把不同时间范围混为一个查询。",
        resume_payload={"operation": "resolve_time_range"},
    )


def _comparison_method_card(
    _understanding: dict[str, Any],
) -> ClarificationCard:
    return _comparison_period_card(_understanding)


def _multi_step_definition_card(
    _understanding: dict[str, Any],
) -> ClarificationCard:
    return ClarificationCard(
        question="请说明要先看哪个指标，以及下一步按什么维度下钻或归因。",
        options=[],
        reason="多步分析需要明确步骤顺序，当前问题还不能形成可执行计划。",
        resume_payload={"operation": "set_intent_type"},
    )


def _intent_unknown_card(
    _understanding: dict[str, Any],
) -> ClarificationCard | None:
    return ClarificationCard(
        question="请确认本次提问想要的分析方式。",
        options=[
            {"label": "查询指标数值", "value": "intent:metric_query"},
            {"label": "查看一段时间的变化趋势", "value": "intent:trend_analysis"},
            {"label": "排名（最高/最低/前N）", "value": "intent:ranking_analysis"},
            {"label": "查看明细列表", "value": "intent:detail_query"},
        ],
        reason="无法确定问题的分析意图，不同意图对应的查询结构完全不同。",
        resume_payload={"operation": "set_intent_type"},
    )


def _select_mode_conflict_card(
    _understanding: dict[str, Any],
) -> ClarificationCard | None:
    return ClarificationCard(
        question="请确认要查看汇总统计还是明细数据。",
        options=[
            {"label": "汇总统计（合计、平均值等）", "value": "intent:metric_query"},
            {"label": "明细列表（逐条记录）", "value": "intent:detail_query"},
        ],
        reason="问题的意图与查询组织方式冲突，必须先确认目标形态。",
        resume_payload={"operation": "set_intent_type"},
    )


def _order_direction_missing_card(
    understanding: dict[str, Any],
) -> ClarificationCard | None:
    intent = _intent_of(understanding)
    target = ""
    ranking = intent.get("ranking")
    if isinstance(ranking, dict):
        target = str(ranking.get("target") or "")
    subject = f"“{target}”" if target else "查询结果"
    return ClarificationCard(
        question=f"请确认{subject}按什么方向排序。",
        options=[
            {"label": "从高到低（最高/最多）", "value": "order:desc"},
            {"label": "从低到高（最低/最少）", "value": "order:asc"},
        ],
        reason="排序方向会改变返回的数据行，不能由系统猜测。",
        resume_payload={"operation": "set_order_direction"},
    )


def _ranking_limit_missing_card(
    _understanding: dict[str, Any],
) -> ClarificationCard | None:
    return ClarificationCard(
        question="请确认排名需要返回多少条结果。",
        options=[
            {"label": "前 5 名", "value": "limit:5"},
            {"label": "前 10 名", "value": "limit:10"},
            {"label": "前 20 名", "value": "limit:20"},
        ],
        reason="排名数量未表达时不能默认取值，需要用户确认。",
        resume_payload={"operation": "set_ranking_limit"},
    )


def _grouping_card(understanding: dict[str, Any]) -> ClarificationCard | None:
    intent = _intent_of(understanding)
    shape = intent.get("query_shape")
    grain = shape.get("time_grain") if isinstance(shape, dict) else None
    subject = f"按{grain}" if grain else "按维度"
    return ClarificationCard(
        question=f"请确认是否需要{subject}分组查看结果。",
        options=[
            {"label": f"是，{subject}分组展示", "value": "grouping:enable"},
            {"label": "否，只看汇总结果", "value": "grouping:disable"},
        ],
        reason="分组声明与查询结构不一致，分组方式会改变结果的行粒度。",
        resume_payload={"operation": "set_grouping"},
    )


def _trend_time_missing_card(
    _understanding: dict[str, Any],
) -> ClarificationCard | None:
    return ClarificationCard(
        question="趋势分析需要时间范围，请提供要分析的时间段（例如：最近30天）。",
        options=[],
        reason="没有时间范围的趋势分析无法确定取数窗口。",
        resume_payload={"operation": "set_time_range_raw"},
    )


def _dimension_role_ambiguous_card(
    understanding: dict[str, Any],
) -> ClarificationCard | None:
    slot = _first_slot_by_role(understanding, "ambiguous")
    if slot is None:
        return None
    name = str(slot.get("name") or "维度").strip() or "维度"
    return ClarificationCard(
        question=f"请确认“{name}”在本次查询中的使用方式。",
        options=[
            {"label": f"按{name}分组查看", "value": f"group_by:{name}"},
            {"label": f"筛选某个具体{name}", "value": f"filter:{name}"},
            {
                "label": f"不使用{name}维度，查看汇总结果",
                "value": f"ignore:{name}",
            },
        ],
        reason=f"“{name}”可能表示分组维度、筛选条件或业务对象，需要先确认后再检索指标口径。",
        resume_payload={
            "operation": "set_dimension_role",
            "slot_name": name,
        },
    )


def _dimension_filter_value_missing_card(
    understanding: dict[str, Any],
) -> ClarificationCard | None:
    slot = _first_filter_slot_without_value(understanding)
    if slot is None:
        return None
    name = str(slot.get("name") or "维度").strip() or "维度"
    return ClarificationCard(
        question=f"请补充需要筛选的具体{name}。",
        options=[],
        reason=f"已确认{name}用于筛选，但还缺少具体筛选值。",
        resume_payload={
            "operation": "set_dimension_filter_value",
            "slot_name": name,
        },
    )


def _dimension_value_ambiguous_card(
    understanding: dict[str, Any],
) -> ClarificationCard | None:
    intent = _intent_of(understanding)
    for slot in intent.get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if (
            str(slot.get("role") or "").lower() == "filter"
            and str(slot.get("value_status") or "").lower() == "ambiguous"
        ):
            name = str(slot.get("name") or "维度").strip() or "维度"
            return ClarificationCard(
                question=f"“{name}”的筛选值不够明确，请提供具体取值。",
                options=[],
                reason=f"{name}的筛选值存在多种理解，直接执行可能取错数据。",
                resume_payload={
                    "operation": "set_dimension_filter_value",
                    "slot_name": name,
                },
            )
    return None


# 注册顺序即处理优先级：时间最先（阻塞一切），其次是意图，最后是槽位细节。
_CARD_BUILDERS = {
    "temporal_clarification_required": _temporal_clarification,
    "time_range_unsupported": _time_range_unsupported_card,
    "comparison_period_missing": _comparison_period_card,
    "comparison_method_missing": _comparison_method_card,
    "multi_step_definition_missing": _multi_step_definition_card,
    "intent_unknown": _intent_unknown_card,
    "select_mode_conflict": _select_mode_conflict_card,
    "trend_time_missing": _trend_time_missing_card,
    "order_direction_missing": _order_direction_missing_card,
    "ranking_order_missing": _order_direction_missing_card,
    "ranking_limit_missing": _ranking_limit_missing_card,
    "time_grain_without_grouping": _grouping_card,
    "group_by_not_declared": _grouping_card,
    "dimension_role_ambiguous": _dimension_role_ambiguous_card,
    "dimension_value_ambiguous": _dimension_value_ambiguous_card,
    "dimension_filter_value_missing": _dimension_filter_value_missing_card,
}


# ---- 拒答建议（无法构造可靠澄清选项的 reason） ----


def _metric_missing_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "当前问题没有说明要查询的指标，暂时无法取数。"
        "请补充指标名称后重新提问，例如：“本月的总销售额是多少？”",
        "metric_missing",
    )


def _rewrite_context_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "这个问题依赖之前的对话内容，但缺少足够的上下文把它补全为独立问题。"
        "请把问题完整描述后再提问，例如：“上个月的华东区销售额是多少？”",
        "rewrite_context_incomplete",
    )


def _intent_conflict_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "当前问题同时表达了相互冲突的分析意图，无法确定唯一查询目标。"
        "请把问题拆开分别提问，例如先问排名、再单独问趋势。",
        "intent_conflict",
    )


def _subject_domain_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "当前问题可能属于多个业务主题，暂时无法确定查询范围。"
        "请说明具体业务场景，或直接指出要查询的指标。",
        "subject_domain_ambiguous",
    )


def _group_by_target_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "问题要求分组查看，但没有说明按什么分组。"
        "请补充分组维度后重问，例如：“按月份查看各月销售额”。",
        "group_by_target_missing",
    )


def _ranking_dimension_refusal(_understanding: dict[str, Any]) -> tuple[str, str]:
    return (
        "排名问题缺少排名对象，无法确定按什么分组比较。"
        "请说明排名对象后重问，例如：“销售额最高的5个门店是哪些”。",
        "ranking_dimension_missing",
    )


def _ranking_target_conflict_refusal(
    _understanding: dict[str, Any],
) -> tuple[str, str]:
    return (
        "问题的排名对象与分组维度不一致，无法确定以哪个维度为准。"
        "请明确唯一排名对象后重新提问。",
        "ranking_target_conflict",
    )


def _dimension_time_value_refusal(
    understanding: dict[str, Any],
) -> tuple[str, str]:
    intent = _intent_of(understanding)
    for slot in intent.get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("role") or "").lower() != "filter":
            continue
        value = slot.get("value")
        if isinstance(value, str) and value.strip():
            name = str(slot.get("name") or "维度")
            return (
                f"“{name}”的筛选值“{value}”看起来是时间表达。"
                "时间要求应放在时间范围中表述，例如：“2026年8月的销售额”，"
                "而不是作为维度筛选值。",
                "dimension_value_is_time_expression",
            )
    return (
        "问题中的筛选值是时间表达，应作为时间范围而不是维度筛选。"
        "请按“时间段 + 指标”的方式重新提问。",
        "dimension_value_is_time_expression",
    )


def _intent_ambiguous_refusal(understanding: dict[str, Any]) -> tuple[str, str]:
    intent = _intent_of(understanding)
    slots = [
        str(slot.get("name") or "")
        for slot in intent.get("dimension_slots") or []
        if isinstance(slot, dict)
        and str(slot.get("role") or "").lower() == "ambiguous"
        and str(slot.get("name") or "")
    ]
    if slots:
        subject = "、".join(f"“{name}”" for name in slots[:3])
        return (
            f"{subject}的用途不明确，可能表示分组、筛选或业务对象。"
            "请换一种更具体的问法，明确这些词的作用后重试。",
            "intent_ambiguous",
        )
    return (
        "问题存在多处歧义，暂时无法确定查询目标。"
        "请把指标、时间、筛选条件表述得更明确后重试。",
        "intent_ambiguous",
    )


def _order_direction_unexpected_refusal(
    _understanding: dict[str, Any],
) -> tuple[str, str]:
    return (
        "问题给出了排序方向但没有表达排序意图，查询结构不一致。"
        "请明确要按哪个指标排序，例如：“按销售额从高到低取前10名”。",
        "order_direction_unexpected",
    )


def _limit_without_order_refusal(
    _understanding: dict[str, Any],
) -> tuple[str, str]:
    return (
        "数量限制需要配合排序使用。"
        "请说明排序方式后重问，例如：“销售额最高的10个商品”。",
        "limit_without_order",
    )


_REFUSAL_ANSWERS = {
    "rewrite_context_incomplete": _rewrite_context_refusal,
    "metric_missing": _metric_missing_refusal,
    "intent_conflict": _intent_conflict_refusal,
    "subject_domain_ambiguous": _subject_domain_refusal,
    "group_by_target_missing": _group_by_target_refusal,
    "ranking_dimension_missing": _ranking_dimension_refusal,
    "ranking_target_conflict": _ranking_target_conflict_refusal,
    "dimension_value_is_time_expression": _dimension_time_value_refusal,
    "intent_ambiguous": _intent_ambiguous_refusal,
    "order_direction_unexpected": _order_direction_unexpected_refusal,
    "limit_without_order": _limit_without_order_refusal,
}


def _intent_of(understanding: dict[str, Any]) -> dict[str, Any]:
    intent = understanding.get("intent")
    return intent if isinstance(intent, dict) else {}


def _first_slot_by_role(
    understanding: dict[str, Any],
    role: str,
) -> dict[str, Any] | None:
    for slot in _intent_of(understanding).get("dimension_slots") or []:
        if isinstance(slot, dict) and str(slot.get("role") or "").lower() == role:
            return slot
    return None


def _first_filter_slot_without_value(
    understanding: dict[str, Any],
) -> dict[str, Any] | None:
    for slot in _intent_of(understanding).get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("role") or "").lower() != "filter":
            continue
        if (
            str(slot.get("value_status") or "").lower() != "provided"
            or slot.get("value") in (None, "")
        ):
            return slot
    return None


__all__ = [
    "ClarificationCard",
    "ClarificationOutcome",
    "ClarificationRefusal",
    "evaluate_clarification",
]
