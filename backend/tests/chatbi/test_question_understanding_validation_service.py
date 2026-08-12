from apps.chatbi.models import QuestionUnderstandingValidationData
from apps.chatbi.services.understanding import validate_question_understanding
from apps.temporal import TemporalPlan


def test_validation_collects_rewrite_intent_metric_and_time_issues():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            rewrite_need_user_input=True,
            rewrite_missing_slots=("context",),
            intent_type="unknown",
            time_range={
                "raw": "发薪日",
                "value_status": "provided",
                "normalized": {"kind": "unsupported"},
            },
            conflict_slots=("time_range",),
        )
    )

    assert result.reason_codes == [
        "rewrite_context_incomplete",
        "intent_unknown",
        "metric_missing",
        "intent_conflict",
        "time_range_unsupported",
    ]
    assert result.clarification_slots == ["context", "intent", "metric", "time_range"]


def test_validation_keeps_dimension_role_and_filter_value_rules_in_one_result():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("客户数",),
            dimension_slots=(
                {
                    "name": "店铺",
                    "role": "ambiguous",
                    "value": None,
                    "value_status": "not_provided",
                },
                {
                    "name": "地区",
                    "role": "filter",
                    "value": " ",
                    "value_status": "provided",
                },
            ),
            ambiguous_slots=("店铺",),
        )
    )

    assert result.reason_codes == [
        "intent_ambiguous",
        "dimension_role_ambiguous",
        "dimension_filter_value_missing",
    ]
    assert result.clarification_slots == ["dimension", "filter_value"]


def test_validation_marks_time_expression_in_dimension_value_as_repair_issue():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("访问人数",),
            dimension_slots=(
                {
                    "name": "店铺",
                    "role": "filter",
                    "value": "最近7天",
                    "value_status": "provided",
                },
            ),
        )
    )

    issue = next(issue for issue in result.issues if issue.category == "repair")
    assert issue.code == "dimension_value_is_time_expression"
    assert issue.details == {
        "slot": "dimension_slots[0].value",
        "dimension": "店铺",
        "value": "最近7天",
    }


def test_validation_uses_authoritative_plan_for_open_time_expression():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("销售额",),
            dimension_slots=(
                {
                    "name": "店铺",
                    "role": "filter",
                    "value": "往前看两周",
                    "value_status": "provided",
                },
            ),
            temporal_plan=TemporalPlan.model_validate(
                {
                    "status": "resolved",
                    "expressions": [
                        {
                            "kind": "rolling_range",
                            "raw": "往前看两周",
                            "source": "rewritten_question",
                            "role": "query_filter",
                            "direction": "past",
                            "amount": 2,
                            "unit": "week",
                            "include_reference_date": True,
                        }
                    ],
                    "ambiguities": [],
                    "confidence": 0.98,
                }
            ),
        )
    )

    assert "dimension_value_is_time_expression" in result.reason_codes


def test_validation_exposes_subject_domain_as_graph_slot_issue():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="detail_query",
            subject_domain={
                "status": "ambiguous",
                "reason": "存在两个候选主题域",
                "candidate_domain_ids": [1, 2],
            },
        )
    )

    issue = next(
        issue for issue in result.issues if issue.code == "subject_domain_ambiguous"
    )
    assert issue.details["candidate_domain_ids"] == [1, 2]


def test_validation_requires_clarification_for_unresolved_temporal_plan():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("销售额",),
            temporal_plan=TemporalPlan.model_validate(
                {
                    "status": "clarification_required",
                    "ambiguities": [
                        {"code": "time_range_amount_missing", "raw": "最近"}
                    ],
                    "confidence": 0.6,
                }
            ),
        )
    )

    assert result.reason_codes == ["temporal_clarification_required"]
    assert result.clarification_slots == ["time_range"]
    assert result.issues[0].details["ambiguity_codes"] == ["time_range_amount_missing"]


def test_validation_accepts_consistent_ranking_shape():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="ranking_analysis",
            metric_mentions=("销售额",),
            dimension_slots=(
                {
                    "name": "门店",
                    "role": "group_by",
                    "value": None,
                    "value_status": "not_provided",
                },
            ),
            query_shape={
                "select_mode": "aggregate",
                "needs_group_by": True,
                "needs_order_by": True,
                "order_direction": "desc",
                "limit": 5,
                "time_grain": None,
            },
        )
    )

    assert result.reason_codes == []


def test_validation_reports_incomplete_ranking_shape_without_filling_it():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="ranking_analysis",
            metric_mentions=("销售额",),
            query_shape={
                "select_mode": "aggregate",
                "needs_group_by": False,
                "needs_order_by": False,
                "order_direction": None,
                "limit": None,
                "time_grain": None,
            },
        )
    )

    assert result.reason_codes == [
        "ranking_dimension_missing",
        "ranking_order_missing",
        "ranking_limit_missing",
    ]
    assert result.clarification_slots == ["dimension", "order", "limit"]


def test_validation_reports_conflicting_query_shape_fields():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("销售额",),
            query_shape={
                "select_mode": "aggregate",
                "needs_group_by": False,
                "needs_order_by": False,
                "order_direction": "desc",
                "limit": 5,
                "time_grain": "day",
            },
        )
    )

    assert result.reason_codes == [
        "order_direction_unexpected",
        "limit_without_order",
        "time_grain_without_grouping",
    ]


def test_validation_rejects_group_by_slot_not_declared_in_query_shape():
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            intent_type="metric_query",
            metric_mentions=("销售额",),
            dimension_slots=(
                {
                    "name": "门店",
                    "role": "group_by",
                    "value": None,
                    "value_status": "not_provided",
                },
            ),
            query_shape={
                "select_mode": "aggregate",
                "needs_group_by": False,
                "needs_order_by": False,
                "order_direction": None,
                "limit": None,
                "time_grain": None,
            },
        )
    )

    assert result.reason_codes == ["group_by_not_declared"]
