from apps.chatbi_workflow.conditions.core import (
    InteractionAnsweredCondition,
    InteractionResponseAnsweredCondition,
    InteractionResponseSkippedCondition,
    InteractionSkippedCondition,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus


def test_interaction_answered_ignores_skipped_response():
    context = WorkflowContext(variables={"intent_response": {"skipped": True}})
    result = NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)

    answered = InteractionAnsweredCondition().evaluate(context, result)
    skipped = InteractionSkippedCondition().evaluate(context, result)

    assert answered.matched is False
    assert skipped.matched is True


def test_interaction_answered_matches_non_skipped_response():
    context = WorkflowContext(variables={"intent_response": {"intent": "metric_query"}})
    result = NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)

    answered = InteractionAnsweredCondition().evaluate(context, result)
    skipped = InteractionSkippedCondition().evaluate(context, result)

    assert answered.matched is True
    assert skipped.matched is False


def test_interaction_answered_matches_slot_response():
    context = WorkflowContext(variables={"slot_response": {"dimension": "店铺", "dimension_usage": "group_by"}})
    result = NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)

    answered = InteractionAnsweredCondition().evaluate(context, result)
    skipped = InteractionSkippedCondition().evaluate(context, result)

    assert answered.matched is True
    assert skipped.matched is False


def test_scoped_interaction_condition_prefers_standard_domain():
    context = WorkflowContext(
        variables={
            "interactions": {
                "ask_metric_selection": {
                    "node_name": "ask_metric_selection",
                    "round": 1,
                    "response": {"metric": 239},
                    "skipped": False,
                }
            },
            "metric_selection": {"skipped": True},
        }
    )
    result = NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)

    answered = InteractionResponseAnsweredCondition(
        node_name="ask_metric_selection",
        legacy_key="metric_selection",
        label="指标选择",
    ).evaluate(context, result)
    skipped = InteractionResponseSkippedCondition(
        node_name="ask_metric_selection",
        legacy_key="metric_selection",
        label="指标选择",
    ).evaluate(context, result)

    assert answered.matched is True
    assert skipped.matched is False
