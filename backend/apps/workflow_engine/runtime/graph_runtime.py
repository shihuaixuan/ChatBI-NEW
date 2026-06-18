from datetime import datetime, timezone

from apps.workflow_engine.domain.context import (
    ContextPatch,
    ControlContext,
    WorkflowContext,
)
from apps.workflow_engine.domain.definition import NodeType
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus
from apps.workflow_engine.domain.interaction import InteractionStatus
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.ports.run_store import RunStore
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.interaction import (
    InteractionError,
    InteractionManager,
)
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.retry import RetryController
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class GraphRuntime:
    """单 Run、单活跃节点的同步图运行时。"""

    def __init__(
        self,
        registry: WorkflowRegistry,
        run_store: RunStore,
        scheduler: NodeScheduler,
        router: ConditionRouter,
        context_patcher: ContextPatcher,
        checkpoint_manager: CheckpointManager,
        lease: InMemoryRunLease,
        retry_controller: RetryController | None = None,
        interaction_manager: InteractionManager | None = None,
    ) -> None:
        self._registry = registry
        self._run_store = run_store
        self._scheduler = scheduler
        self._router = router
        self._context_patcher = context_patcher
        self._checkpoints = checkpoint_manager
        self._lease = lease
        self._retry = retry_controller or RetryController()
        self._interactions = interaction_manager

    def create_run(
        self,
        run_id: str,
        definition_name: str,
        definition_version: str,
        context: WorkflowContext,
    ) -> WorkflowRun:
        definition = self._registry.get(definition_name, definition_version)
        now = datetime.now(timezone.utc)
        initial_context = context.model_copy(deep=True)
        initial_context.control = ControlContext(current_node=definition.start_node)
        run = WorkflowRun(
            run_id=run_id,
            definition_name=definition_name,
            definition_version=definition_version,
            definition_digest=self._registry.get_digest(definition_name, definition_version),
            status=RunStatus.CREATED,
            current_node=definition.start_node,
            context=initial_context,
            created_at=now,
            updated_at=now,
        )
        created = self._run_store.create(run)
        self._checkpoints.publish_event(
            created,
            "run.created",
            public_payload={
                "status": created.status.value,
                "question": created.context.request.get("question"),
            },
        )
        return created

    def execute(self, run_id: str) -> WorkflowRun:
        with self._lease.acquire(run_id):
            run = self._run_store.get(run_id)
            definition = self._registry.get(run.definition_name, run.definition_version)
            current_digest = self._registry.get_digest(run.definition_name, run.definition_version)
            if current_digest != run.definition_digest:
                raise RuntimeError("DEFINITION_DIGEST_MISMATCH")

            if run.status is RunStatus.CREATED:
                run.status = RunStatus.RUNNING
                run.updated_at = datetime.now(timezone.utc)
                run = self._run_store.save(run, expected_version=run.version)
                self._checkpoints.publish_event(run, "run.started")

            while run.status is RunStatus.RUNNING:
                elapsed_ms = (datetime.now(timezone.utc) - run.created_at).total_seconds() * 1000
                if elapsed_ms > definition.policies.run_timeout_ms:
                    run.status = RunStatus.FAILED
                    return self._checkpoints.fail(run, "RUN_TIMEOUT_EXCEEDED")
                if run.context.control.executed_nodes >= definition.policies.max_nodes_per_run:
                    run.status = RunStatus.FAILED
                    return self._checkpoints.fail(run, "MAX_NODES_PER_RUN_EXCEEDED")
                if run.current_node is None:
                    raise RuntimeError("CURRENT_NODE_MISSING")

                node = definition.nodes[run.current_node]
                visit_count = run.context.control.loop_iterations.get(node.name, 0) + 1
                run.context.control.loop_iterations[node.name] = visit_count
                if visit_count > definition.policies.max_loop_iterations:
                    run.status = RunStatus.FAILED
                    return self._checkpoints.fail(
                        run,
                        "LOOP_ITERATION_LIMIT_EXCEEDED",
                        node_name=node.name,
                    )
                self._checkpoints.publish_event(run, "node.started", node_name=node.name)
                result = self._execute_with_retry(run, node, definition.policies.default_retry_policy)

                if result.status is NodeResultStatus.FAILED:
                    run.status = RunStatus.FAILED
                    error_code = result.error.code if result.error is not None else "NODE_FAILED"
                    return self._checkpoints.fail(run, error_code, node_name=node.name)

                if result.status is NodeResultStatus.WAITING_INPUT:
                    return self._pause_for_interaction(run, node.name, result)

                updated_context = self._context_patcher.apply(run.context, result.patch)
                updated_context.control.executed_nodes += 1
                updated_context.control.previous_node = node.name
                run.context = updated_context

                if node.type is NodeType.TERMINAL:
                    # 终止节点本身已经执行成功，此时才允许把 Run 标记为完成。
                    run.status = RunStatus.SUCCEEDED
                    run.current_node = node.name
                    run.context.control.current_node = node.name
                    return self._checkpoints.save_progress(
                        run,
                        node_name=node.name,
                        completed=True,
                    )

                route = self._router.select(definition, node, run.context, result)
                run.current_node = route.target
                run.context.control.current_node = route.target
                run = self._checkpoints.save_progress(run, node_name=node.name, route=route)

            return run

    def resume(
        self,
        run_id: str,
        interaction_id: str,
        response: dict,
        tenant_id: int,
        user_id: int,
    ) -> WorkflowRun:
        """校验并合并人工回答，再按服务端图定义选择恢复节点。"""

        if self._interactions is None:
            raise InteractionError("INTERACTION_NOT_SUPPORTED", "Runtime 未配置交互管理器")

        with self._lease.acquire(run_id):
            run = self._run_store.get(run_id)
            interaction = self._interactions.get(interaction_id)
            if interaction.run_id != run_id:
                raise InteractionError("INTERACTION_NOT_FOUND", interaction_id)
            if (
                run.context.request.get("tenant_id") != tenant_id
                or run.context.request.get("user_id") != user_id
            ):
                raise InteractionError("INTERACTION_ACCESS_DENIED", interaction_id)
            if interaction.status is not InteractionStatus.PENDING:
                raise InteractionError("INTERACTION_ALREADY_ANSWERED", interaction_id)
            if run.context.control.pending_interaction_id != interaction_id:
                raise InteractionError("INTERACTION_NOT_FOUND", interaction_id)

            answered = self._interactions.answer(interaction_id, response)
            patch_values = dict.fromkeys(answered.allowed_update_paths, response)
            run.context = self._context_patcher.apply(
                run.context,
                ContextPatch(set_values=patch_values),
            )
            definition = self._registry.get(run.definition_name, run.definition_version)
            current_node = definition.nodes[run.current_node]
            route = self._router.select(
                definition,
                current_node,
                run.context,
                NodeExecutionResult(status=NodeResultStatus.SUCCEEDED),
            )
            run.status = RunStatus.RUNNING
            run.current_node = route.target
            run.context.control.previous_node = current_node.name
            run.context.control.current_node = route.target
            run.context.control.pending_interaction_id = None
            self._checkpoints.resume(run)

        # 释放恢复事务的 Lease 后再进入标准执行循环，避免同一进程自锁。
        return self.execute(run_id)

    def _execute_with_retry(self, run, node, default_policy) -> NodeExecutionResult:
        policy = node.retry_policy or default_policy
        result: NodeExecutionResult | None = None
        for attempt in range(1, policy.max_attempts + 1):
            result = self._scheduler.execute(run.run_id, node, attempt, run.context)
            if result.status is not NodeResultStatus.FAILED:
                return result
            if result.error is None or not result.error.retryable or attempt >= policy.max_attempts:
                return result
            self._checkpoints.publish_event(
                run,
                "node.retrying",
                node_name=node.name,
                public_payload={"attempt": attempt + 1, "error_code": result.error.code},
            )
            self._retry.wait(policy, attempt)
        if result is None:
            raise RuntimeError("NODE_EXECUTION_NOT_ATTEMPTED")
        return result

    def _pause_for_interaction(
        self,
        run: WorkflowRun,
        node_name: str,
        result: NodeExecutionResult,
    ) -> WorkflowRun:
        if self._interactions is None or result.interaction is None:
            run.status = RunStatus.FAILED
            return self._checkpoints.fail(run, "INTERACTION_NOT_SUPPORTED", node_name=node_name)
        interaction = self._interactions.create(run.run_id, node_name, result.interaction)
        run.context.control.executed_nodes += 1
        run.context.control.previous_node = node_name
        run.context.control.pending_interaction_id = interaction.interaction_id
        run.status = RunStatus.WAITING_INPUT
        return self._checkpoints.pause(run, node_name=node_name)
