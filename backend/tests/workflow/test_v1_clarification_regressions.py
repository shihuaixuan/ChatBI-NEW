"""Step 0 缺陷回归测试：超时语义（A1）、交互条件串扰（A2）、澄清轮次（A4）。

这些用例锁定的都是修复后的正确行为；修复前的行为分别是：
- A1：澄清卡片停留超过 run_timeout_ms 后提交回答，Run 以 RUN_TIMEOUT_EXCEEDED 失败；
- A2：先答过一次澄清后，再跳过另一个澄清会被残留回答劫持路由，最终撞上
  LOOP_ITERATION_LIMIT_EXCEEDED；
- A4：澄清点反复触发时没有轮次上限，以开发者向的循环失败码终止整个 Run。
"""

from datetime import timedelta

from apps.chatbi.orchestration.graph.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.conditions.core import register_chatbi_conditions
from apps.chatbi.orchestration.graph.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from sqlbot_platform.workflow_engine.domain.context import WorkflowContext
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
from sqlbot_platform.workflow_engine.runtime.interaction import InteractionManager
from sqlbot_platform.workflow_engine.runtime.lease import InMemoryRunLease
from sqlbot_platform.workflow_engine.runtime.router import ConditionRouter
from sqlbot_platform.workflow_engine.runtime.scheduler import NodeScheduler


class TrackingGateway(PlaceholderChatBICapabilityGateway):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        return super().invoke(capability, request, idempotency_key)


class AlwaysNeedRewriteGateway(TrackingGateway):
    """无论用户补充多少次，问题重写始终要求继续澄清。"""

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        if capability == "question.rewrite":
            self.calls.append(capability)
            return {
                "rewritten_question": str(request.get("request", {}).get("question", "")),
                "need_user_input": True,
                "missing_slots": ["metric"],
                "image_profile_hint": None,
            }
        return super().invoke(capability, request, idempotency_key)


def _runtime(gateway: TrackingGateway) -> tuple[GraphRuntime, InMemoryRunStore]:
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())
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
        interaction_manager=InteractionManager(),
    )
    return runtime, store


def _create_run(runtime: GraphRuntime, run_id: str, question: str):
    return runtime.create_run(
        run_id,
        "chatbi",
        "v1",
        WorkflowContext(request={"question": question, "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )


def test_resume_after_long_user_wait_does_not_hit_run_timeout():
    """A1：用户在澄清卡片上停留任意久，恢复执行不应因墙钟时间超时失败。"""

    gateway = TrackingGateway()
    runtime, store = _runtime(gateway)
    _create_run(runtime, "regress-a1", "需要澄清的问题")

    paused = runtime.execute("regress-a1")
    assert paused.status is RunStatus.WAITING_INPUT

    # 模拟用户离开 1 小时后才回答。
    paused.created_at = paused.created_at - timedelta(hours=1)
    store.save(paused, expected_version=paused.version)

    resumed = runtime.resume(
        "regress-a1",
        paused.context.control.pending_interaction_id or "",
        {"metric": "sales_amount"},
        tenant_id=10,
        user_id=20,
    )

    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.current_node == "finish"


def test_skip_second_clarification_after_answering_first_one_reaches_fallback_answer():
    """A2：先回答重写澄清、再跳过意图澄清时，跳过必须生效并走兜底回答。"""

    gateway = TrackingGateway()
    runtime, _ = _runtime(gateway)
    # 问题同时触发重写澄清（"需要澄清"）与意图澄清（"意图不明"）。
    _create_run(runtime, "regress-a2", "需要澄清 且 意图不明 的问题")

    first_pause = runtime.execute("regress-a2")
    assert first_pause.status is RunStatus.WAITING_INPUT
    assert first_pause.current_node == "ask_rewrite_clarification"

    second_pause = runtime.resume(
        "regress-a2",
        first_pause.context.control.pending_interaction_id or "",
        {"metric": "销售额"},
        tenant_id=10,
        user_id=20,
    )
    assert second_pause.status is RunStatus.WAITING_INPUT
    assert second_pause.current_node == "ask_intent_clarification"

    outcome = runtime.resume(
        "regress-a2",
        second_pause.context.control.pending_interaction_id or "",
        {"skipped": True},
        tenant_id=10,
        user_id=20,
    )

    # 修复前：残留的 rewrite_response 让 interaction.answered 抢先命中，
    # 流程被拉回 recognize_intent 反复澄清直到 LOOP_ITERATION_LIMIT_EXCEEDED。
    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    assert "sql.generate" not in gateway.calls
    assert gateway.calls.count("answer.generate") == 1


def test_rewrite_clarification_rounds_are_capped_and_degrade_to_answer():
    """A4：澄清点最多提问 2 轮，用尽后走兜底回答而不是循环失败。"""

    gateway = AlwaysNeedRewriteGateway()
    runtime, _ = _runtime(gateway)
    _create_run(runtime, "regress-a4", "需要澄清的问题")

    outcome = runtime.execute("regress-a4")
    rounds = 0
    while outcome.status is RunStatus.WAITING_INPUT:
        rounds += 1
        assert rounds <= 5, "澄清轮次未被限制"
        outcome = runtime.resume(
            "regress-a4",
            outcome.context.control.pending_interaction_id or "",
            {"metric": "销售额"},
            tenant_id=10,
            user_id=20,
        )

    assert rounds == 2
    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    assert gateway.calls.count("answer.generate") == 1
