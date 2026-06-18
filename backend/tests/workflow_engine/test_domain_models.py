from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from apps.workflow_engine.domain.artifact import ArtifactRef, WorkflowArtifact
from apps.workflow_engine.domain.checkpoint import WorkflowCheckpoint
from apps.workflow_engine.domain.context import (
    ContextPatch,
    ControlContext,
    WorkflowContext,
)
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowPolicies,
)
from apps.workflow_engine.domain.errors import NodeError
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
    NodeResultStatus,
)
from apps.workflow_engine.domain.interaction import (
    InteractionRequest,
    InteractionStatus,
)
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="chatbi",
        version="v1",
        start_node="start",
        nodes={
            "start": NodeDefinition(name="start", type=NodeType.TRANSFORM, handler="start"),
            "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="finish"),
        },
        edges=[EdgeDefinition(source="start", target="finish")],
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def test_workflow_definition_accepts_versioned_graph_contract():
    definition = _definition()

    assert definition.name == "chatbi"
    assert definition.policies.max_nodes_per_run == 100
    assert definition.nodes["finish"].type is NodeType.TERMINAL


def test_workflow_definition_rejects_empty_name_and_duplicate_node_names():
    with pytest.raises(ValidationError):
        _definition().model_copy(update={"name": ""}, deep=True).model_validate(
            {**_definition().model_dump(), "name": ""}
        )

    with pytest.raises(ValidationError, match="节点字典键必须与节点名称一致"):
        WorkflowDefinition(
            name="chatbi",
            version="v1",
            start_node="start",
            nodes={"other": NodeDefinition(name="start", type=NodeType.TRANSFORM, handler="start")},
            edges=[],
            input_schema={},
            output_schema={},
        )


def test_retry_policy_and_workflow_policies_reject_non_positive_limits():
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)

    with pytest.raises(ValidationError):
        WorkflowPolicies(max_nodes_per_run=0)


def test_context_uses_fixed_namespaces_and_patch_is_declarative():
    artifact = ArtifactRef(
        artifact_id="artifact-1",
        kind="sql",
        content_type="text/sql",
        size=12,
        digest="sha256:test",
    )
    context = WorkflowContext(
        request={"question": "销售额", "tenant_id": 1},
        conversation={"messages": []},
        variables={"intent": "query"},
        artifacts={"sql": artifact},
        control=ControlContext(current_node="start"),
    )
    patch = ContextPatch(
        set_values={"variables.metric": "revenue"},
        append_values={"conversation.messages": [{"role": "user", "content": "销售额"}]},
        remove_paths=["variables.intent"],
        artifact_updates={"result": artifact},
    )

    assert context.request["tenant_id"] == 1
    assert patch.set_values["variables.metric"] == "revenue"
    assert patch.artifact_updates["result"].artifact_id == "artifact-1"


def test_node_execution_result_requires_error_for_failed_status():
    with pytest.raises(ValidationError, match="失败结果必须包含错误"):
        NodeExecutionResult(status=NodeResultStatus.FAILED)

    result = NodeExecutionResult(
        status=NodeResultStatus.FAILED,
        error=NodeError(code="CAPABILITY_TIMEOUT", message="能力调用超时", retryable=True),
    )

    assert result.error is not None
    assert result.error.retryable is True


def test_node_execution_request_carries_stable_idempotency_key():
    request = NodeExecutionRequest(
        run_id="run-1",
        node_name="query_understand",
        attempt=1,
        idempotency_key="run-1:query_understand:1",
        inputs={"question": "销售额"},
        context_view={"request": {"tenant_id": 1}},
    )

    assert request.idempotency_key == "run-1:query_understand:1"


def test_run_checkpoint_event_interaction_and_artifact_are_engine_models():
    now = datetime.now(timezone.utc)
    context = WorkflowContext(request={"question": "销售额"})
    run = WorkflowRun(
        run_id="run-1",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="sha256:def",
        status=RunStatus.RUNNING,
        current_node="start",
        context=context,
        created_at=now,
        updated_at=now,
    )
    checkpoint = WorkflowCheckpoint(
        checkpoint_id="cp-1",
        run_id=run.run_id,
        sequence=1,
        node_name="start",
        context=context,
        definition_digest=run.definition_digest,
        created_at=now,
    )
    event = WorkflowEvent(
        event_id="event-1",
        run_id=run.run_id,
        sequence=1,
        event_type="run.started",
        created_at=now,
    )
    interaction = InteractionRequest(
        interaction_id="interaction-1",
        run_id=run.run_id,
        node_name="clarification",
        status=InteractionStatus.PENDING,
        response_schema={"type": "object"},
        allowed_update_paths=["conversation.answers"],
        created_at=now,
    )
    artifact = WorkflowArtifact(
        artifact_id="artifact-1",
        run_id=run.run_id,
        kind="query_result",
        content_type="application/json",
        size=2,
        digest="sha256:data",
        storage_uri="memory://artifact-1",
        created_at=now,
    )

    assert checkpoint.context.request["question"] == "销售额"
    assert event.public_payload == {}
    assert interaction.status is InteractionStatus.PENDING
    assert artifact.storage_uri == "memory://artifact-1"
