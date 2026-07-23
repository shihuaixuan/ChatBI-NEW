from sqlbot_platform.workflow_engine.domain.context import ContextPatch, WorkflowContext
from sqlbot_platform.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
)
from sqlbot_platform.workflow_engine.domain.execution import (
    NodeExecutionResult,
    NodeResultStatus,
)
from sqlbot_platform.workflow_engine.domain.run import RunStatus
from sqlbot_platform.workflow_engine.infrastructure.memory import (
    InMemoryEventPublisher,
    InMemoryRunStore,
)
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.registry.workflow_registry import WorkflowRegistry
from sqlbot_platform.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from sqlbot_platform.workflow_engine.runtime.context_patcher import ContextPatcher
from sqlbot_platform.workflow_engine.runtime.graph_runtime import GraphRuntime
from sqlbot_platform.workflow_engine.runtime.lease import InMemoryRunLease
from sqlbot_platform.workflow_engine.runtime.router import ConditionRouter
from sqlbot_platform.workflow_engine.runtime.scheduler import NodeScheduler


class RecordingHandler:
    def __init__(self, name: str, calls: list[str], patch: ContextPatch | None = None) -> None:
        self.name = name
        self.calls = calls
        self.patch = patch or ContextPatch()

    def execute(self, request):
        self.calls.append(self.name)
        return NodeExecutionResult(status=NodeResultStatus.SUCCEEDED, patch=self.patch)


def _runtime() -> tuple[GraphRuntime, InMemoryRunStore, InMemoryEventPublisher, CheckpointManager, list[str]]:
    calls: list[str] = []
    handlers = HandlerRegistry()
    handlers.register("start", RecordingHandler("start", calls, ContextPatch(set_values={"variables.ready": True})))
    handlers.register("decision", RecordingHandler("decision", calls))
    handlers.register("finish", RecordingHandler("finish", calls, ContextPatch(set_values={"variables.answer": "ok"})))
    conditions = ConditionRegistry()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(
        WorkflowDefinition(
            name="runtime",
            version="v1",
            start_node="start",
            nodes={
                "start": NodeDefinition(name="start", type=NodeType.TRANSFORM, handler="start"),
                "decision": NodeDefinition(name="decision", type=NodeType.DECISION, handler="decision"),
                "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="finish"),
            },
            edges=[
                EdgeDefinition(source="start", target="decision"),
                EdgeDefinition(source="decision", target="finish"),
            ],
            input_schema={},
            output_schema={},
        )
    )
    run_store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    checkpoints = CheckpointManager(run_store, events)
    runtime = GraphRuntime(
        registry=registry,
        run_store=run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=checkpoints,
        lease=InMemoryRunLease(),
    )
    return runtime, run_store, events, checkpoints, calls


def test_graph_runtime_executes_terminal_node_and_persists_path():
    runtime, run_store, events, checkpoints, calls = _runtime()
    created = runtime.create_run("run-1", "runtime", "v1", WorkflowContext(request={"question": "销售额"}))

    outcome = runtime.execute(created.run_id)
    stored = run_store.get(created.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert calls == ["start", "decision", "finish"]
    assert stored.context.variables == {"ready": True, "answer": "ok"}
    assert stored.context.control.executed_nodes == 3
    assert stored.current_node == "finish"
    assert [checkpoint.node_name for checkpoint in checkpoints.list("run-1")] == ["start", "decision", "finish"]
    assert [event.event_type for event in events.list("run-1")] == [
        "run.created",
        "run.started",
        "node.started",
        "node.succeeded",
        "node.routed",
        "node.started",
        "node.succeeded",
        "node.routed",
        "node.started",
        "node.succeeded",
        "run.succeeded",
    ]


def test_graph_runtime_rejects_second_concurrent_lease():
    lease = InMemoryRunLease()

    with lease.acquire("run-1"):
        try:
            with lease.acquire("run-1"):
                raise AssertionError("不应获得第二个租约")
        except RuntimeError as exc:
            assert "RUN_LEASE_CONFLICT" in str(exc)
