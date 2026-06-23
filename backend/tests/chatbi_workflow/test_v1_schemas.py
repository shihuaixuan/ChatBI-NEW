from apps.chatbi_workflow.nodes.v1 import ChatBIV1CapabilityNode
from apps.chatbi_workflow.schemas.v1 import (
    QuestionClassificationInput,
    QuestionClassificationOutput,
)
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeResultStatus,
)


class BrokenGateway:
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        return {"reason": "missing_category"}


def test_v1_capability_node_rejects_output_that_does_not_match_schema():
    node = ChatBIV1CapabilityNode(
        BrokenGateway(),
        capability="question.classify",
        output_path="variables.classification",
        output_model=QuestionClassificationOutput,
    )

    result = node.execute(
        NodeExecutionRequest(
            run_id="schema-run-1",
            node_name="classify_question",
            attempt=1,
            idempotency_key="schema-run-1:classify_question:1:1",
            context_view={"request": {"question": "销售额"}},
        )
    )

    assert result.status is NodeResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "QUESTION_CLASSIFY_FAILED"


def test_v1_question_classification_input_requires_question_identity_and_dataset():
    schema = QuestionClassificationInput.model_json_schema()

    assert set(schema["required"]) == {"question", "tenant_id", "user_id", "dataset_id"}
