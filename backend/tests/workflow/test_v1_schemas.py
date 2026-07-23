from apps.chatbi.orchestration.graph.nodes.v1 import ChatBIV1CapabilityNode
from apps.chatbi.orchestration.graph.schemas.v1 import (
    QuestionClassificationInput,
    QuestionClassificationOutput,
)
from sqlbot_platform.workflow_engine.domain.execution import (
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

    # A3 降级语义：不合规输出不再终止 Run，而是转结构化 node_failure，
    # 由图中的 node.degraded 边路由到解释性回答；输出路径本身不被写入。
    assert result.status is NodeResultStatus.SUCCEEDED
    assert "variables.classification" not in result.patch.set_values
    failure = result.patch.set_values["variables.node_failure"]
    assert failure["error_code"] == "QUESTION_CLASSIFY_FAILED"
    assert failure["node"] == "classify_question"


def test_v1_question_classification_input_requires_question_identity_and_dataset():
    schema = QuestionClassificationInput.model_json_schema()

    assert set(schema["required"]) == {"question", "tenant_id", "user_id", "dataset_id"}
