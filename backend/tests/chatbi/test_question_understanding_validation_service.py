from apps.chatbi.models import QuestionUnderstandingValidationData
from apps.chatbi.services.understanding import validate_question_understanding


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

    issue = next(issue for issue in result.issues if issue.code == "subject_domain_ambiguous")
    assert issue.details["candidate_domain_ids"] == [1, 2]
