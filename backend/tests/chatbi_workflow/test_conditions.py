from apps.chatbi_workflow.conditions.core import (
    KnowledgeCrossModelCondition,
    SlotClarificationNeededCondition,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus


def test_slot_clarification_needed_condition_handles_subject_domain():
    decision = SlotClarificationNeededCondition().evaluate(
        WorkflowContext(variables={"intent": {"validation": {"clarification_required": True}}}),
        NodeExecutionResult(status=NodeResultStatus.SUCCEEDED),
    )

    assert decision.matched is True
    assert decision.reason_code == "SLOT_CLARIFICATION_NEEDED"


def test_slot_clarification_needed_condition_ignores_raw_dimension_slots_without_validation_contract():
    decision = SlotClarificationNeededCondition().evaluate(
        WorkflowContext(
            variables={
                "intent": {
                    "dimension_slots": [
                        {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
                    ]
                }
            }
        ),
        NodeExecutionResult(status=NodeResultStatus.SUCCEEDED),
    )

    assert decision.matched is False
    assert decision.reason_code == "SLOT_CLARIFICATION_NOT_NEEDED"


def test_knowledge_cross_model_condition_routes_to_split_confirmation():
    decision = KnowledgeCrossModelCondition().evaluate(
        WorkflowContext(
            variables={
                "knowledge": {
                    "hit": True,
                    "status": "cross_model",
                    "multi_query_plans": [{"model_id": 10}, {"model_id": 11}],
                }
            }
        ),
        NodeExecutionResult(status=NodeResultStatus.SUCCEEDED),
    )

    assert decision.matched is True
    assert decision.reason_code == "KNOWLEDGE_CROSS_MODEL"
