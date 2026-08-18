from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import pytest

from apps.chatbi.errors import TemporalInterpretationError
from apps.chatbi.models import QuestionModelResponse, TimeRange
from apps.chatbi.services.understanding import (
    QuestionUnderstandingService,
    StructuredModelService,
    TemporalInterpretationService,
    build_temporal_clarification_options,
    compare_temporal_shadow,
)
from apps.temporal import (
    TemporalContext,
    TemporalPlan,
    normalize_time_range_payload,
)


class SequenceQuestionModel:
    """按顺序返回结构化结果，便于验证修复调用和旁路调用次数。"""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[tuple[str, str]] = []

    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionModelResponse:
        self.calls.append((system_prompt, user_prompt))
        payload = self._payloads.pop(0)
        return QuestionModelResponse(
            content=orjson.dumps(payload).decode(),
            usage_metadata={"total_tokens": 10},
        )


class FailingQuestionModel:
    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionModelResponse:
        raise TimeoutError("模型调用超时")


class InvalidJSONQuestionModel:
    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionModelResponse:
        return QuestionModelResponse(
            content="模型输出不是 JSON",
            usage_metadata={"total_tokens": 10},
        )


def _context() -> TemporalContext:
    return TemporalContext(
        reference_at=datetime(
            2026,
            7,
            31,
            12,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
        timezone="Asia/Shanghai",
    )


def _rolling_plan(*, amount: int = 7) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "status": "resolved",
        "expressions": [
            {
                "kind": "rolling_range",
                "raw": "最近7天",
                "source": "rewritten_question",
                "start_offset": 0,
                "end_offset": 4,
                "role": "query_filter",
                "direction": "past",
                "amount": amount,
                "unit": "day",
                "include_reference_date": True,
            }
        ],
        "grouping": None,
        "comparison": None,
        "ambiguities": [],
        "confidence": 0.98,
    }


def _open_rolling_plan() -> dict[str, Any]:
    return {
        **_rolling_plan(amount=2),
        "expressions": [
            {
                "kind": "rolling_range",
                "raw": "往前看两周",
                "source": "rewritten_question",
                "start_offset": 0,
                "end_offset": 5,
                "role": "query_filter",
                "direction": "past",
                "amount": 2,
                "unit": "week",
                "include_reference_date": True,
            }
        ],
    }


def _clarification_plan() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "status": "clarification_required",
        "expressions": [],
        "grouping": None,
        "comparison": None,
        "ambiguities": [
            {
                "code": "time_range_amount_missing",
                "raw": "最近",
            }
        ],
        "confidence": 0.6,
    }


def _confirmed_rolling_plan() -> dict[str, Any]:
    plan = _rolling_plan()
    plan["expressions"] = [
        {
            **plan["expressions"][0],
            "source": "user_confirmation",
        }
    ]
    return plan


def _rewrite_payload(question: str) -> dict[str, Any]:
    return {
        "message_type": "new_question",
        "rewritten_question": question,
        "inherited_context": {},
        "need_user_input": False,
        "missing_slots": [],
        "confidence": 0.98,
    }


def _intent_payload() -> dict[str, Any]:
    return {
        "intent_type": "metric_query",
        "confidence": 0.95,
        "metric_mentions": ["销售额"],
        "dimension_mentions": [],
        "dimension_slots": [],
        "time_mentions": ["最近7天"],
        "time_range": {"raw": "最近7天", "value_status": "provided"},
        "filter_mentions": [],
        "required_slot_types": [],
        "query_shape": {"select_mode": "aggregate"},
        "ambiguous_slots": [],
        "conflict_slots": [],
    }


def _empty_dimensions() -> dict[str, Any]:
    return {
        "dimension_mentions": [],
        "dimension_slots": [],
        "residual_filter_mentions": [],
        "ambiguous_slots": [],
        "conflict_slots": [],
    }


def test_temporal_interpretation_retries_once_with_precise_feedback() -> None:
    model = SequenceQuestionModel(
        [
            _rolling_plan(amount=0),
            _rolling_plan(),
        ]
    )
    service = TemporalInterpretationService(StructuredModelService(model))

    outcome = service.interpret(
        rewritten_question="最近7天销售额",
        metric_mentions=["销售额"],
        time_mentions=["最近7天"],
        temporal_context=_context(),
    )

    assert outcome.plan.expressions[0].kind == "rolling_range"
    assert outcome.usage_metadata["total_tokens"] == 20
    assert len(model.calls) == 2
    retry_payload = orjson.loads(model.calls[1][1])
    assert retry_payload["repair_feedback"]["reason_code"] == (
        "TEMPORAL_MODEL_OUTPUT_INVALID"
    )
    assert retry_payload["repair_feedback"]["validation_errors"][0]["loc"] == [
        "expressions",
        0,
        "rolling_range",
        "amount",
    ]


def test_temporal_interpretation_receives_upstream_analysis_context() -> None:
    model = SequenceQuestionModel([_rolling_plan()])
    service = TemporalInterpretationService(StructuredModelService(model))

    service.interpret(
        rewritten_question="最近7天销售额环比增长率",
        metric_mentions=["销售额"],
        time_mentions=["最近7天"],
        temporal_context=_context(),
        analysis_context={
            "intent_type": "trend_analysis",
            "comparison": {"method": "mom", "base": "前7天", "compare": ["最近7天"]},
            "expressions": [
                {
                    "op": "growth",
                    "display_name": "环比增长率",
                    "over": "time_comparison",
                }
            ],
        },
    )

    payload = orjson.loads(model.calls[0][1])
    assert payload["analysis_context"]["intent_type"] == "trend_analysis"
    assert payload["analysis_context"]["expressions"][0]["op"] == "growth"


def test_temporal_interpretation_repairs_explicit_mentions_rejected_by_model() -> None:
    model = SequenceQuestionModel(
        [
            {
                "schema_version": "1",
                "status": "unsupported",
                "expressions": [],
                "grouping": None,
                "comparison": None,
                "ambiguities": [
                    {
                        "code": "time_expression_unsupported",
                        "raw": "2026年6月",
                    }
                ],
                "confidence": 0.2,
            }
        ]
    )
    service = TemporalInterpretationService(StructuredModelService(model))

    outcome = service.interpret(
        rewritten_question="对比2026年6月与2026年5月总订单数",
        metric_mentions=["总订单数"],
        time_mentions=["2026年6月", "2026年5月"],
        temporal_context=_context(),
        analysis_context={
            "intent_type": "comparison_analysis",
            "expressions": [{"op": "compare"}],
        },
    )

    assert outcome.plan.status == "resolved"
    assert [item.raw for item in outcome.plan.expressions] == [
        "2026年6月",
        "2026年5月",
    ]
    assert outcome.plan.comparison is not None
    assert outcome.plan.comparison.method == "custom"
    assert len(model.calls) == 1


def test_temporal_interpretation_repairs_explicit_mentions_after_invalid_json() -> None:
    service = TemporalInterpretationService(
        StructuredModelService(InvalidJSONQuestionModel())
    )

    outcome = service.interpret(
        rewritten_question="对比店铺100021在2026年6月与2026年5月的总订单数。",
        metric_mentions=["总订单数"],
        time_mentions=["2026年6月", "2026年5月"],
        temporal_context=_context(),
        analysis_context={
            "intent_type": "comparison_analysis",
            "expressions": [{"op": "compare"}],
        },
    )

    assert outcome.plan.status == "resolved"
    assert outcome.plan.comparison is not None
    assert outcome.plan.comparison.method == "custom"


def test_temporal_interpretation_rejects_two_invalid_outputs() -> None:
    model = SequenceQuestionModel(
        [
            _rolling_plan(amount=0),
            _rolling_plan(amount=0),
        ]
    )
    service = TemporalInterpretationService(StructuredModelService(model))

    with pytest.raises(TemporalInterpretationError) as exc_info:
        service.interpret(
            rewritten_question="最近7天销售额",
            metric_mentions=["销售额"],
            time_mentions=["最近7天"],
            temporal_context=_context(),
        )

    assert exc_info.value.code == "TEMPORAL_MODEL_OUTPUT_INVALID"
    assert exc_info.value.usage_metadata["total_tokens"] == 20
    assert len(model.calls) == 2


def test_temporal_interpretation_exposes_model_call_failure() -> None:
    service = TemporalInterpretationService(
        StructuredModelService(FailingQuestionModel())
    )

    with pytest.raises(TemporalInterpretationError) as exc_info:
        service.interpret(
            rewritten_question="最近7天销售额",
            metric_mentions=["销售额"],
            time_mentions=["最近7天"],
            temporal_context=_context(),
        )

    assert exc_info.value.code == "TEMPORAL_MODEL_CALL_FAILED"
    assert exc_info.value.usage_metadata["total_tokens"] == 0


def test_temporal_interpretation_accepts_open_expression_plan() -> None:
    model = SequenceQuestionModel([_open_rolling_plan()])
    service = TemporalInterpretationService(StructuredModelService(model))

    outcome = service.interpret(
        rewritten_question="往前看两周销售额",
        metric_mentions=["销售额"],
        time_mentions=[],
        temporal_context=_context(),
    )

    expression = outcome.plan.expressions[0]
    assert expression.kind == "rolling_range"
    assert expression.raw == "往前看两周"
    assert expression.amount == 2
    assert expression.unit == "week"
    assert len(model.calls) == 1


def test_temporal_interpretation_for_execution_resolves_trusted_range() -> None:
    model = SequenceQuestionModel([_open_rolling_plan()])
    service = TemporalInterpretationService(StructuredModelService(model))

    result, usage = service.interpret_for_execution(
        rewritten_question="往前看两周销售额",
        metric_mentions=["销售额"],
        time_mentions=[],
        temporal_context=_context(),
    )

    assert result.plan.status == "resolved"
    assert result.resolved_plan is not None
    assert result.time_range.normalized == {
        "kind": "absolute_range",
        "start": "2026-07-18",
        "end_exclusive": "2026-08-01",
        "timezone": "Asia/Shanghai",
        "source_raw": "往前看两周",
    }
    assert result.interpretation_source == "model"
    assert usage["total_tokens"] == 10


def test_temporal_interpretation_repairs_metric_internal_time_role() -> None:
    query_filter_plan = {
        **_rolling_plan(),
        "expressions": [
            {
                **_rolling_plan()["expressions"][0],
                "raw": "近7天",
                "start_offset": 2,
                "end_offset": 5,
                "role": "query_filter",
            }
        ],
    }
    metric_definition_plan = {
        **query_filter_plan,
        "expressions": [
            {
                **query_filter_plan["expressions"][0],
                "role": "metric_definition",
            }
        ],
    }
    model = SequenceQuestionModel(
        [
            query_filter_plan,
            metric_definition_plan,
        ]
    )
    service = TemporalInterpretationService(StructuredModelService(model))

    outcome = service.interpret(
        rewritten_question="查看近7天销量",
        metric_mentions=["近7天销量"],
        time_mentions=[],
        temporal_context=_context(),
    )

    assert outcome.plan.expressions[0].role == "metric_definition"
    retry_payload = orjson.loads(model.calls[1][1])
    assert retry_payload["repair_feedback"]["validation_errors"] == [
        {
            "type": "business_rule",
            "loc": [],
            "msg": "TEMPORAL_METRIC_DEFINITION_ROLE_REQUIRED",
        }
    ]


def test_temporal_interpretation_repairs_false_metric_time_ambiguity() -> None:
    question = "2026年6月30日查看当前库存件数和近30天销量"
    expressions = [
        {
            "kind": "absolute_date",
            "raw": "2026年6月30日",
            "source": "rewritten_question",
            "start_offset": 0,
            "end_offset": 10,
            "role": "query_filter",
            "date": "2026-06-30",
        },
        {
            "kind": "relative_date",
            "raw": "当前",
            "source": "rewritten_question",
            "start_offset": 12,
            "end_offset": 14,
            "role": "metric_definition",
            "offset_days": 0,
        },
        {
            "kind": "rolling_range",
            "raw": "近30天",
            "source": "rewritten_question",
            "start_offset": 19,
            "end_offset": 23,
            "role": "metric_definition",
            "direction": "past",
            "amount": 30,
            "unit": "day",
            "include_reference_date": True,
        },
    ]
    ambiguous_plan = {
        "status": "clarification_required",
        "expressions": expressions,
        "ambiguities": [
            {
                "code": "time_range_conflict",
                "raw": "当前",
            }
        ],
        "confidence": 0.5,
    }
    resolved_plan = {
        **ambiguous_plan,
        "status": "resolved",
        "ambiguities": [],
        "confidence": 0.95,
    }
    model = SequenceQuestionModel([ambiguous_plan, resolved_plan])
    service = TemporalInterpretationService(StructuredModelService(model))

    outcome = service.interpret(
        rewritten_question=question,
        metric_mentions=["当前库存件数", "近30天销量"],
        time_mentions=["2026年6月30日"],
        temporal_context=_context(),
    )

    assert outcome.plan.status == "resolved"
    assert [item.role for item in outcome.plan.expressions] == [
        "query_filter",
        "metric_definition",
        "metric_definition",
    ]
    retry_payload = orjson.loads(model.calls[1][1])
    assert retry_payload["repair_feedback"]["validation_errors"] == [
        {
            "type": "business_rule",
            "loc": [],
            "msg": "TEMPORAL_METRIC_DEFINITION_AMBIGUITY_FORBIDDEN",
        }
    ]


def test_temporal_shadow_matches_legacy_absolute_range() -> None:
    temporal_context = _context()
    legacy_time_range = TimeRange.model_validate(
        normalize_time_range_payload(
            {"raw": "最近7天", "value_status": "provided"},
            temporal_context=temporal_context,
        )
    )

    observation = compare_temporal_shadow(
        plan=TemporalPlan.model_validate(_rolling_plan()),
        legacy_time_range=legacy_time_range,
        temporal_context=temporal_context,
    )

    assert observation.status == "matched"
    assert observation.difference_codes == ()
    assert observation.resolved_plan is not None
    assert observation.resolved_plan.filters[0].start.isoformat() == "2026-07-25"
    assert (
        observation.resolved_plan.filters[0].end_exclusive.isoformat() == "2026-08-01"
    )


def test_question_understanding_records_shadow_without_replacing_time_range() -> None:
    question = "最近7天销售额"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload(),
            _empty_dimensions(),
            _rolling_plan(),
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        temporal_shadow_enabled=True,
    ).understand(
        question=question,
        datasource_id=13,
        temporal_context=_context(),
    )

    assert outcome.output.intent.time_range.normalized == {
        "kind": "absolute_range",
        "start": "2026-07-25",
        "end_exclusive": "2026-08-01",
        "timezone": "Asia/Shanghai",
        "source_raw": "最近7天",
    }
    assert outcome.output.intent.time_range.interpretation_source == "jionlp"
    assert outcome.temporal_shadow is not None
    assert outcome.temporal_shadow.status == "matched"
    assert outcome.usage_metadata["total_tokens"] == 40
    assert len(model.calls) == 4


def test_temporal_shadow_model_error_keeps_legacy_time_range() -> None:
    question = "最近7天销售额"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            _intent_payload(),
            _rolling_plan(amount=0),
            _rolling_plan(amount=0),
        ]
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        temporal_shadow_enabled=True,
    ).understand(
        question=question,
        datasource_id=13,
        temporal_context=_context(),
    )

    assert outcome.output.intent.time_range.normalized is not None
    assert outcome.output.intent.time_range.normalized["start"] == "2026-07-25"
    assert outcome.temporal_shadow is not None
    assert outcome.temporal_shadow.status == "model_error"
    assert outcome.temporal_shadow.error_code == "TEMPORAL_MODEL_OUTPUT_INVALID"
    assert outcome.usage_metadata["total_tokens"] == 40
    assert len(model.calls) == 4


def test_question_understanding_authority_replaces_legacy_time_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "往前看两周销售额"
    intent_payload = {
        **_intent_payload(),
        "time_mentions": ["往前看两周"],
        "time_range": {"raw": "往前看两周", "value_status": "provided"},
    }
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            intent_payload,
            _empty_dimensions(),
            _open_rolling_plan(),
        ]
    )
    monkeypatch.setattr(
        "apps.chatbi.services.understanding.understanding_service.normalize_time_range_payload",
        lambda *args, **kwargs: pytest.fail("权威路径不得调用旧时间原文解析"),
    )

    outcome = QuestionUnderstandingService(
        model_client=model,
        temporal_authority_enabled=True,
    ).understand(
        question=question,
        datasource_id=13,
        temporal_context=_context(),
    )

    assert outcome.output.intent.time_range.normalized == {
        "kind": "absolute_range",
        "start": "2026-07-18",
        "end_exclusive": "2026-08-01",
        "timezone": "Asia/Shanghai",
        "source_raw": "往前看两周",
    }
    assert outcome.output.intent.time_range.interpretation_source == "model"
    assert outcome.output.temporal_interpretation is not None
    assert outcome.output.temporal_interpretation.plan.status == "resolved"
    assert outcome.output.validation.status == "valid"
    assert outcome.temporal_shadow is None
    assert outcome.usage_metadata["total_tokens"] == 40


def test_question_understanding_temporal_clarification_only_reruns_time_task() -> None:
    question = "最近销售额"
    intent_payload = {
        **_intent_payload(),
        "time_mentions": ["最近"],
        "time_range": {"raw": "最近", "value_status": "provided"},
    }
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            intent_payload,
            _empty_dimensions(),
            _clarification_plan(),
            _confirmed_rolling_plan(),
        ]
    )
    service = QuestionUnderstandingService(
        model_client=model,
        temporal_authority_enabled=True,
    )

    initial = service.understand(
        question=question,
        datasource_id=13,
        temporal_context=_context(),
    )
    resolved = service.resolve_temporal_clarification(
        understanding=initial.output.model_dump(mode="json"),
        answer={"text": "最近7天"},
        temporal_context=_context(),
    )

    assert initial.output.validation.status == "clarification_required"
    assert initial.output.validation.reason_codes == ["temporal_clarification_required"]
    assert initial.output.intent.time_range.value_status == "not_provided"
    assert resolved.output.validation.status == "valid"
    assert resolved.output.temporal_interpretation is not None
    assert (
        resolved.output.temporal_interpretation.interpretation_source
        == "user_confirmation"
    )
    assert resolved.output.intent.time_range.normalized is not None
    assert resolved.output.intent.time_range.normalized["start"] == "2026-07-25"
    assert len(model.calls) == 5
    confirmation_payload = orjson.loads(model.calls[-1][1])
    assert confirmation_payload["user_confirmation"] == "最近7天"


def test_question_understanding_structured_time_option_does_not_call_model() -> None:
    question = "最近销售额"
    model = SequenceQuestionModel(
        [
            _rewrite_payload(question),
            {
                **_intent_payload(),
                "time_mentions": ["最近"],
                "time_range": {"raw": "最近", "value_status": "provided"},
            },
            _empty_dimensions(),
            _clarification_plan(),
        ]
    )
    service = QuestionUnderstandingService(
        model_client=model,
        temporal_authority_enabled=True,
    )
    initial = service.understand(
        question=question,
        datasource_id=13,
        temporal_context=_context(),
    )
    selected = build_temporal_clarification_options({"time_range_amount_missing"})[0]

    resolved = service.resolve_temporal_clarification(
        understanding=initial.output.model_dump(mode="json"),
        answer={"selections": [selected]},
        temporal_context=_context(),
    )

    assert resolved.output.validation.status == "valid"
    assert resolved.output.intent.time_range.normalized is not None
    assert resolved.output.intent.time_range.normalized["start"] == "2026-07-25"
    assert resolved.usage_metadata == {}
    assert len(model.calls) == 4

    tampered = orjson.loads(orjson.dumps(selected))
    tampered["value"]["temporal_plan"]["expressions"][0]["amount"] = 8
    with pytest.raises(TemporalInterpretationError) as exc_info:
        service.resolve_temporal_clarification(
            understanding=initial.output.model_dump(mode="json"),
            answer={"selections": [tampered]},
            temporal_context=_context(),
        )
    assert exc_info.value.code == "TEMPORAL_CONFIRMATION_OPTION_NOT_ALLOWED"
    assert len(model.calls) == 4


def test_question_understanding_rejects_conflicting_temporal_modes() -> None:
    with pytest.raises(ValueError, match="TEMPORAL_INTERPRETATION_MODE_CONFLICT"):
        QuestionUnderstandingService(
            model_client=SequenceQuestionModel([]),
            temporal_shadow_enabled=True,
            temporal_authority_enabled=True,
        )
