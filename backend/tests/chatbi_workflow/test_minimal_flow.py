from apps.chatbi_workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.chatbi_workflow.conditions.core import register_chatbi_conditions
from apps.chatbi_workflow.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
    register_chatbi_minimal_handlers,
)
from apps.workflow_engine.domain.context import WorkflowContext
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
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class FakeGateway(ChatBICapabilityGateway):
    def __init__(self, allow_permission: bool = True, sql_valid: bool = True) -> None:
        self.allow_permission = allow_permission
        self.sql_valid = sql_valid
        self.calls: list[str] = []

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "query.understand":
            return {"normalized_question": request["question"]}
        if capability == "schema.retrieve":
            return {"tables": ["orders"], "fields": ["amount"]}
        if capability == "sql.generate":
            return {"sql": "select sum(amount) from orders"}
        if capability == "sql.validate":
            return {"valid": self.sql_valid, "reason": "ok" if self.sql_valid else "invalid"}
        if capability == "permission.apply":
            return {"allowed": self.allow_permission, "reason": "ok"}
        if capability == "sql.execute":
            return {"rows": [{"sum": 100}]}
        if capability == "answer.generate":
            return {"answer": "销售额为 100"}
        raise AssertionError(f"未预期的能力调用: {capability}")


def _runtime(gateway: FakeGateway) -> GraphRuntime:
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_minimal_definition())
    store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    return GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, events),
        lease=InMemoryRunLease(),
    )


def test_minimal_chatbi_flow_executes_fake_capabilities_in_safe_order():
    gateway = FakeGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-run-1",
        "chatbi",
        "minimal-v1",
        WorkflowContext(request={"question": "最近 7 天销售额", "datasource_id": 1}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.context.variables["answer"]["answer"] == "销售额为 100"
    assert gateway.calls == [
        "query.understand",
        "schema.retrieve",
        "sql.generate",
        "sql.validate",
        "permission.apply",
        "sql.execute",
        "answer.generate",
    ]


def test_minimal_chatbi_flow_does_not_execute_sql_when_permission_denied():
    gateway = FakeGateway(allow_permission=False)
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-run-denied",
        "chatbi",
        "minimal-v1",
        WorkflowContext(request={"question": "最近 7 天销售额", "datasource_id": 1}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert "sql.execute" not in gateway.calls
    assert gateway.calls[-1] == "answer.generate"


def test_minimal_chatbi_flow_does_not_execute_sql_when_validation_failed():
    gateway = FakeGateway(sql_valid=False)
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-run-invalid-sql",
        "chatbi",
        "minimal-v1",
        WorkflowContext(request={"question": "最近 7 天销售额", "datasource_id": 1}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert "sql.execute" not in gateway.calls
    assert gateway.calls[-1] == "answer.generate"
