from datetime import timedelta

from apps.workflow_engine.domain.context import ContextPatch, WorkflowContext
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowPolicies,
)
from apps.workflow_engine.domain.errors import NodeError
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
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.retry import RetryController
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class RetryOnceHandler:
    def __init__(self) -> None:
        self.attempts: list[int] = []

    def execute(self, request):
        self.attempts.append(request.attempt)
        if request.attempt == 1:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="TEMPORARY", message="临时失败", retryable=True),
            )
        return NodeExecutionResult(
            status=NodeResultStatus.SUCCEEDED,
            patch=ContextPatch(set_values={"variables.retried": True}),
        )


class PermanentFailureHandler:
    def execute(self, request):
        return NodeExecutionResult(
            status=NodeResultStatus.FAILED,
            error=NodeError(code="PERMANENT", message="永久失败", retryable=False),
        )


class SuccessHandler:
    def execute(self, request):
        return NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)


def _build_runtime(handler, max_nodes: int = 10):
    handlers = HandlerRegistry()
    handlers.register("work", handler)
    handlers.register("finish", SuccessHandler())
    conditions = ConditionRegistry()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(
        WorkflowDefinition(
            name="failure",
            version="v1",
            start_node="work",
            nodes={
                "work": NodeDefinition(
                    name="work",
                    type=NodeType.CAPABILITY,
                    handler="work",
                    retry_policy=RetryPolicy(max_attempts=2),
                ),
                "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="finish"),
            },
            edges=[EdgeDefinition(source="work", target="finish")],
            input_schema={},
            output_schema={},
            policies=WorkflowPolicies(max_nodes_per_run=max_nodes),
        )
    )
    store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    runtime = GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, events),
        lease=InMemoryRunLease(),
        retry_controller=RetryController(sleep=lambda _: None),
    )
    return runtime, store, events


def test_runtime_retries_retryable_failure_with_new_attempt_key():
    handler = RetryOnceHandler()
    runtime, _, events = _build_runtime(handler)
    runtime.create_run("run-retry", "failure", "v1", WorkflowContext())

    outcome = runtime.execute("run-retry")

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.context.variables["retried"] is True
    assert handler.attempts == [1, 2]
    assert "node.retrying" in [event.event_type for event in events.list("run-retry")]


def test_runtime_marks_run_failed_without_following_normal_default_edge():
    runtime, store, events = _build_runtime(PermanentFailureHandler())
    runtime.create_run("run-failed", "failure", "v1", WorkflowContext())

    outcome = runtime.execute("run-failed")

    assert outcome.status is RunStatus.FAILED
    assert store.get("run-failed").current_node == "work"
    assert [event.event_type for event in events.list("run-failed")][-2:] == ["node.failed", "run.failed"]


def test_runtime_enforces_max_nodes_budget():
    runtime, _, events = _build_runtime(SuccessHandler(), max_nodes=1)
    runtime.create_run("run-budget", "failure", "v1", WorkflowContext())

    outcome = runtime.execute("run-budget")

    assert outcome.status is RunStatus.FAILED
    assert [event.public_payload.get("error_code") for event in events.list("run-budget")][-1] == "MAX_NODES_PER_RUN_EXCEEDED"


def test_runtime_enforces_per_node_loop_budget():
    handlers = HandlerRegistry()
    handlers.register("loop", SuccessHandler())
    conditions = ConditionRegistry()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(
        WorkflowDefinition(
            name="loop",
            version="v1",
            start_node="loop",
            nodes={"loop": NodeDefinition(name="loop", type=NodeType.TRANSFORM, handler="loop")},
            edges=[EdgeDefinition(source="loop", target="loop")],
            input_schema={},
            output_schema={},
            policies=WorkflowPolicies(max_nodes_per_run=10, max_loop_iterations=2),
        )
    )
    store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    runtime = GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, events),
        lease=InMemoryRunLease(),
    )
    runtime.create_run("run-loop", "loop", "v1", WorkflowContext())

    outcome = runtime.execute("run-loop")

    assert outcome.status is RunStatus.FAILED
    assert events.list("run-loop")[-1].public_payload["error_code"] == "LOOP_ITERATION_LIMIT_EXCEEDED"


def test_runtime_enforces_total_run_timeout_on_active_execution_time():
    runtime, store, events = _build_runtime(SuccessHandler())
    created = runtime.create_run("run-timeout", "failure", "v1", WorkflowContext())
    created.context.control.active_ms = 300_001
    store.save(created, expected_version=created.version)

    outcome = runtime.execute("run-timeout")

    assert outcome.status is RunStatus.FAILED
    assert events.list("run-timeout")[-1].public_payload["error_code"] == "RUN_TIMEOUT_EXCEEDED"


def test_runtime_ignores_wall_clock_age_of_run():
    """超时预算不包含等待用户输入的墙钟时间：旧 Run 恢复执行不应立即超时。"""

    runtime, store, _ = _build_runtime(SuccessHandler())
    created = runtime.create_run("run-old", "failure", "v1", WorkflowContext())
    created.created_at = created.created_at - timedelta(hours=1)
    store.save(created, expected_version=created.version)

    outcome = runtime.execute("run-old")

    assert outcome.status is RunStatus.SUCCEEDED
