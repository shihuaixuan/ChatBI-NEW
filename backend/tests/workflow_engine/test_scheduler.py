import pytest

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.definition import NodeDefinition, NodeType
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.runtime.scheduler import NodeScheduler, SchedulerError


class CountingHandler:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)


def _node(handler: str = "count") -> NodeDefinition:
    return NodeDefinition(
        name="understand",
        type=NodeType.CAPABILITY,
        handler=handler,
        input_mapping={"question": "request.question"},
    )


def test_scheduler_builds_request_and_deduplicates_same_idempotency_key():
    registry = HandlerRegistry()
    handler = CountingHandler()
    registry.register("count", handler)
    scheduler = NodeScheduler(registry)
    context = WorkflowContext(request={"question": "销售额"})

    first = scheduler.execute("run-1", _node(), 1, context)
    second = scheduler.execute("run-1", _node(), 1, context)

    assert first.status is NodeResultStatus.SUCCEEDED
    assert second.status is NodeResultStatus.SUCCEEDED
    assert handler.calls == 1


def test_scheduler_reports_missing_handler_and_invalid_result():
    registry = HandlerRegistry()
    scheduler = NodeScheduler(registry)

    with pytest.raises(SchedulerError) as missing:
        scheduler.execute("run-1", _node("missing"), 1, WorkflowContext(request={"question": "销售额"}))
    assert missing.value.code == "HANDLER_NOT_FOUND"

    class InvalidHandler:
        def execute(self, request):
            return {"unexpected": True}

    registry.register("invalid", InvalidHandler())
    with pytest.raises(SchedulerError) as invalid:
        scheduler.execute("run-1", _node("invalid"), 1, WorkflowContext(request={"question": "销售额"}))
    assert invalid.value.code == "INVALID_NODE_RESULT"


def test_scheduler_reports_mapping_failure_as_stable_error():
    registry = HandlerRegistry()
    registry.register("count", CountingHandler())

    with pytest.raises(SchedulerError) as exc_info:
        NodeScheduler(registry).execute("run-1", _node(), 1, WorkflowContext())

    assert exc_info.value.code == "NODE_INPUT_MAPPING_FAILED"
