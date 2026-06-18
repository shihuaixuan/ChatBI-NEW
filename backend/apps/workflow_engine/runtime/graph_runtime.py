from datetime import datetime, timezone

from apps.workflow_engine.domain.context import ControlContext, WorkflowContext
from apps.workflow_engine.domain.definition import NodeType
from apps.workflow_engine.domain.execution import NodeResultStatus
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.ports.run_store import RunStore
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.lease import InMemoryRunLease
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
    ) -> None:
        self._registry = registry
        self._run_store = run_store
        self._scheduler = scheduler
        self._router = router
        self._context_patcher = context_patcher
        self._checkpoints = checkpoint_manager
        self._lease = lease

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
        self._checkpoints.publish_event(created, "run.created")
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
                if run.context.control.executed_nodes >= definition.policies.max_nodes_per_run:
                    raise RuntimeError("MAX_NODES_PER_RUN_EXCEEDED")
                if run.current_node is None:
                    raise RuntimeError("CURRENT_NODE_MISSING")

                node = definition.nodes[run.current_node]
                self._checkpoints.publish_event(run, "node.started", node_name=node.name)
                result = self._scheduler.execute(run.run_id, node, 1, run.context)
                if result.status is not NodeResultStatus.SUCCEEDED:
                    raise RuntimeError(f"UNSUPPORTED_NODE_RESULT: {result.status.value}")

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
