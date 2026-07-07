import pytest

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
)
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus
from apps.workflow_engine.domain.run import RunStatus
from apps.workflow_engine.infrastructure.memory import (
    InMemoryEventPublisher,
    InMemoryRunStore,
)
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.registry.definition_validator import DefinitionValidator
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.graph_runtime import GraphRuntime
from apps.workflow_engine.runtime.interaction import (
    InteractionError,
    InteractionManager,
)
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class ClarificationHandler:
    def __init__(self, allowed_update_paths: list[str] | None = None) -> None:
        self._allowed_update_paths = allowed_update_paths or ["conversation.clarification"]

    def execute(self, request):
        return NodeExecutionResult(
            status=NodeResultStatus.WAITING_INPUT,
            interaction={
                "prompt": "请选择指标",
                "response_schema": {
                    "type": "object",
                    "required": ["metric"],
                    "properties": {"metric": {"type": "string"}},
                },
                "allowed_update_paths": self._allowed_update_paths,
            },
        )


class FinishHandler:
    def __init__(self) -> None:
        self.inputs = []

    def execute(self, request):
        self.inputs.append(request.context_view)
        return NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)


def _runtime(
    *,
    interaction_node_name: str = "clarify",
    allowed_update_paths: list[str] | None = None,
):
    handlers = HandlerRegistry()
    finish = FinishHandler()
    handlers.register("clarify", ClarificationHandler(allowed_update_paths))
    handlers.register("finish", finish)
    conditions = ConditionRegistry()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(
        WorkflowDefinition(
            name="interaction",
            version="v1",
            start_node=interaction_node_name,
            nodes={
                interaction_node_name: NodeDefinition(
                    name=interaction_node_name,
                    type=NodeType.INTERACTION,
                    handler="clarify",
                ),
                "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="finish"),
            },
            edges=[EdgeDefinition(source=interaction_node_name, target="finish")],
            input_schema={},
            output_schema={},
        )
    )
    store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    interactions = InteractionManager()
    runtime = GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, events),
        lease=InMemoryRunLease(),
        interaction_manager=interactions,
    )
    return runtime, store, events, interactions, finish


def test_runtime_pauses_and_resumes_same_run_from_server_selected_edge():
    runtime, store, events, interactions, finish = _runtime()
    runtime.create_run(
        "run-interaction",
        "interaction",
        "v1",
        WorkflowContext(request={"tenant_id": 1, "user_id": 7}),
    )

    waiting = runtime.execute("run-interaction")
    interaction = interactions.get(waiting.context.control.pending_interaction_id)
    outcome = runtime.resume(
        run_id="run-interaction",
        interaction_id=interaction.interaction_id,
        response={"metric": "revenue"},
        tenant_id=1,
        user_id=7,
    )

    assert waiting.status is RunStatus.WAITING_INPUT
    assert outcome.status is RunStatus.SUCCEEDED
    assert store.get("run-interaction").context.conversation["clarification"] == {"metric": "revenue"}
    assert finish.inputs[-1]["conversation"]["clarification"] == {"metric": "revenue"}
    assert "run.resumed" in [event.event_type for event in events.list("run-interaction")]


def test_resume_writes_structured_record_for_standard_interaction_path():
    runtime, _, _, _, _ = _runtime(
        interaction_node_name="ask_metric_selection",
        allowed_update_paths=[
            "variables.interactions.ask_metric_selection",
            "variables.metric_selection",
        ],
    )
    run = runtime.create_run(
        "run-standard-interaction",
        "interaction",
        "v1",
        WorkflowContext(request={"tenant_id": 1, "user_id": 7}),
    )
    waiting = runtime.execute(run.run_id)

    outcome = runtime.resume(
        waiting.run_id,
        waiting.context.control.pending_interaction_id or "",
        {"metric": "revenue"},
        tenant_id=1,
        user_id=7,
    )

    variables = outcome.context.variables
    record = variables["interactions"]["ask_metric_selection"]
    assert record["node_name"] == "ask_metric_selection"
    assert record["round"] == 1
    assert record["response"] == {"metric": "revenue"}
    assert record["skipped"] is False
    assert "answered_at" in record
    assert variables["metric_selection"] == {"metric": "revenue"}


def test_runtime_rejects_duplicate_invalid_and_wrong_user_responses():
    runtime, _, _, interactions, _ = _runtime()
    runtime.create_run(
        "run-invalid",
        "interaction",
        "v1",
        WorkflowContext(request={"tenant_id": 1, "user_id": 7}),
    )
    waiting = runtime.execute("run-invalid")
    interaction_id = waiting.context.control.pending_interaction_id

    with pytest.raises(InteractionError) as invalid:
        runtime.resume("run-invalid", interaction_id, {}, tenant_id=1, user_id=7)
    assert invalid.value.code == "INTERACTION_RESPONSE_INVALID"

    with pytest.raises(InteractionError) as wrong_user:
        runtime.resume("run-invalid", interaction_id, {"metric": "revenue"}, tenant_id=1, user_id=8)
    assert wrong_user.value.code == "INTERACTION_ACCESS_DENIED"

    runtime.resume("run-invalid", interaction_id, {"metric": "revenue"}, tenant_id=1, user_id=7)
    with pytest.raises(InteractionError) as duplicate:
        runtime.resume("run-invalid", interaction_id, {"metric": "profit"}, tenant_id=1, user_id=7)
    assert duplicate.value.code == "INTERACTION_ALREADY_ANSWERED"
