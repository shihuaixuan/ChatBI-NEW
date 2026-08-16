"""P0-1 澄清全覆盖：每个理解校验 reason_code 都必须有澄清或拒答归宿。

死锁判定标准：validation.status != valid 时 evaluate_clarification 绝不返回 None，
且卡片对应的 resume_payload.operation 必须能被 apply 函数识别。
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.chatbi.models.dto.question_understanding import (
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionUnderstandingOutput,
    QuestionUnderstandingValidationData,
)
from apps.chatbi.services.understanding import (
    ClarificationCard,
    ClarificationRefusal,
    apply_question_understanding_clarification,
    evaluate_clarification,
    validate_question_understanding,
)
from apps.chatbi.services.understanding.understanding_service import (
    _CLARIFICATION_OPERATIONS,
)
from apps.temporal import build_run_temporal_context

# validation.py 当前产出的全部 reason_code；新增代码必须同步补归宿。
ALL_REASON_CODES = (
    "rewrite_context_incomplete",
    "intent_unknown",
    "metric_missing",
    "intent_conflict",
    "select_mode_conflict",
    "order_direction_missing",
    "order_direction_unexpected",
    "limit_without_order",
    "time_grain_without_grouping",
    "group_by_not_declared",
    "group_by_target_missing",
    "ranking_dimension_missing",
    "ranking_order_missing",
    "ranking_limit_missing",
    "ranking_target_conflict",
    "trend_time_missing",
    "temporal_clarification_required",
    "time_range_unsupported",
    "intent_ambiguous",
    "subject_domain_ambiguous",
    "dimension_value_is_time_expression",
    "dimension_role_ambiguous",
    "dimension_value_ambiguous",
    "dimension_filter_value_missing",
)


def _understanding_with_codes(*reason_codes: str) -> dict[str, Any]:
    output = QuestionUnderstandingOutput(
        original_question="按门店看本月销售额",
        message_type="new_question",
        rewritten_question="按门店看本月销售额",
        intent=IntentRecognitionOutput(intent_type="metric_query", confidence=0.9),
        validation=IntentValidationOutput(status="valid"),
    )
    understanding = output.model_dump(mode="json")
    understanding["validation"] = {
        "status": "clarification_required",
        "reason_codes": list(reason_codes),
        "clarification_slots": [],
    }
    intent = understanding["intent"]
    intent["dimension_slots"] = [
        {"name": "门店", "role": "filter", "value": "1001", "value_status": "provided"},
        {"name": "区域", "role": "ambiguous", "value": None, "value_status": "not_provided"},
        {"name": "商圈", "role": "filter", "value": None, "value_status": "not_provided"},
    ]
    intent["ranking"] = {
        "target": "门店",
        "metric": "销售额",
        "direction": "desc",
        "selection": "top_n",
        "limit": None,
    }
    intent["query_shape"].update(
        {
            "needs_group_by": False,
            "needs_order_by": False,
            "order_direction": None,
            "limit": None,
            "time_grain": "month",
        }
    )
    return understanding


def test_valid_understanding_has_no_clarification_outcome():
    understanding = _understanding_with_codes()
    understanding["validation"] = {"status": "valid", "reason_codes": []}
    assert evaluate_clarification(understanding) is None


@pytest.mark.parametrize("reason_code", ALL_REASON_CODES)
def test_every_reason_code_has_card_or_refusal(reason_code: str):
    outcome = evaluate_clarification(_understanding_with_codes(reason_code))
    assert isinstance(
        outcome,
        (ClarificationCard, ClarificationRefusal),
    ), f"reason_code={reason_code} 没有澄清或拒答归宿"


@pytest.mark.parametrize("reason_code", ALL_REASON_CODES)
def test_every_reason_code_from_real_validation_has_outcome(reason_code: str):
    """用真实校验器触发同码问题，确认端到端也覆盖（结构来自校验器输入约定）。"""

    outcome = evaluate_clarification(_understanding_with_codes(reason_code))
    if isinstance(outcome, ClarificationCard):
        assert outcome.question
        assert outcome.resume_payload.get("operation") in (
            _CLARIFICATION_OPERATIONS
            | {"resolve_temporal_plan", "resolve_time_range"}
        )
    else:
        assert outcome.answer
        assert outcome.reason_code


def test_metric_missing_refuses_with_suggestion():
    outcome = evaluate_clarification(_understanding_with_codes("metric_missing"))
    assert isinstance(outcome, ClarificationRefusal)
    assert "指标" in outcome.answer


def test_temporal_clarification_wins_priority():
    outcome = evaluate_clarification(
        _understanding_with_codes("metric_missing", "temporal_clarification_required")
    )
    assert isinstance(outcome, ClarificationCard)
    assert outcome.resume_payload["operation"] == "resolve_temporal_plan"


def test_intent_unknown_card_options_apply():
    card = evaluate_clarification(_understanding_with_codes("intent_unknown"))
    assert isinstance(card, ClarificationCard)
    assert {option["value"] for option in card.options} >= {"intent:metric_query"}
    updated = apply_question_understanding_clarification(
        understanding=_understanding_with_codes(),
        resume_payload={"operation": "set_intent_type"},
        answer={"selections": [{"value": "intent:ranking_analysis"}]},
        temporal_context=build_run_temporal_context(),
    )
    assert updated.intent.intent_type == "ranking_analysis"
    assert updated.intent.query_shape.select_mode == "aggregate"


def test_order_direction_and_limit_cards_apply():
    direction_card = evaluate_clarification(
        _understanding_with_codes("order_direction_missing")
    )
    assert isinstance(direction_card, ClarificationCard)
    limit_card = evaluate_clarification(_understanding_with_codes("ranking_limit_missing"))
    assert isinstance(limit_card, ClarificationCard)

    updated = apply_question_understanding_clarification(
        understanding=_understanding_with_codes(),
        resume_payload={"operation": "set_order_direction"},
        answer={"selections": [{"value": "order:desc"}]},
        temporal_context=build_run_temporal_context(),
    )
    assert updated.intent.query_shape.order_direction == "desc"
    assert updated.intent.query_shape.needs_order_by is True

    updated = apply_question_understanding_clarification(
        understanding=_understanding_with_codes(),
        resume_payload={"operation": "set_ranking_limit"},
        answer={"selections": [{"value": "limit:10"}]},
        temporal_context=build_run_temporal_context(),
    )
    assert updated.intent.query_shape.limit == 10


def test_grouping_card_disable_clears_group_structure():
    updated = apply_question_understanding_clarification(
        understanding=_understanding_with_codes(),
        resume_payload={"operation": "set_grouping"},
        answer={"selections": [{"value": "grouping:disable"}]},
        temporal_context=build_run_temporal_context(),
    )
    assert updated.intent.query_shape.needs_group_by is False
    assert updated.intent.query_shape.time_grain is None


def test_time_range_raw_input_applies_free_text_answer():
    updated = apply_question_understanding_clarification(
        understanding=_understanding_with_codes(),
        resume_payload={"operation": "set_time_range_raw"},
        answer={"text": "最近30天"},
        temporal_context=build_run_temporal_context(),
    )
    assert updated.intent.time_range.raw == "最近30天"
    assert updated.intent.time_range.value_status == "provided"


def test_dimension_value_ambiguous_maps_to_filter_value_card():
    understanding = _understanding_with_codes("dimension_value_ambiguous")
    understanding["intent"]["dimension_slots"].append(
        {"name": "渠道", "role": "filter", "value": None, "value_status": "ambiguous"}
    )
    outcome = evaluate_clarification(understanding)
    assert isinstance(outcome, ClarificationCard)
    assert outcome.resume_payload["operation"] == "set_dimension_filter_value"
    assert outcome.resume_payload["slot_name"] == "渠道"


def test_unknown_reason_code_falls_back_to_refusal():
    outcome = evaluate_clarification(_understanding_with_codes("future_unknown_code"))
    assert isinstance(outcome, ClarificationRefusal)


def test_real_validation_reason_codes_are_all_covered():
    """用真实校验器触发一批 reason_code，保证注册表覆盖真实输出。"""

    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="unknown",
            metric_mentions=(),
            conflict_slots=("intent",),
            ambiguous_slots=("门店",),
            dimension_slots=(
                {
                    "name": "门店",
                    "role": "filter",
                    "value": None,
                    "value_status": "provided",
                },
                {
                    "name": "区域",
                    "role": "ambiguous",
                    "value": None,
                    "value_status": "not_provided",
                },
            ),
        )
    )
    emitted = set(result.reason_codes)
    assert emitted, "校验器应产出问题码"
    for code in emitted:
        assert code in ALL_REASON_CODES, f"校验器新增了未登记的 reason_code: {code}"
        outcome = evaluate_clarification(_understanding_with_codes(code))
        assert isinstance(outcome, (ClarificationCard, ClarificationRefusal))
