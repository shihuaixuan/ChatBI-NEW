from datetime import datetime, timezone
from uuid import uuid4

from apps.workflow_engine.domain.checkpoint import WorkflowCheckpoint
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.domain.run import WorkflowRun
from apps.workflow_engine.ports.event_publisher import EventPublisher
from apps.workflow_engine.ports.run_store import RunStore
from apps.workflow_engine.runtime.router import RouteDecision


class CheckpointManager:
    """集中维护 Run 游标、Checkpoint 与事件的提交顺序。

    P0 内存实现不能提供数据库事务，但所有调用都经过同一入口。P1 只需替换该入口的
    Unit of Work，就能把 Run、Checkpoint、NodeExecution 与 Event Outbox 原子提交。
    """

    def __init__(self, run_store: RunStore, events: EventPublisher) -> None:
        self._run_store = run_store
        self._events = events
        self._checkpoints: dict[str, list[WorkflowCheckpoint]] = {}

    def publish_event(
        self,
        run: WorkflowRun,
        event_type: str,
        node_name: str | None = None,
        public_payload: dict | None = None,
    ) -> None:
        sequence = len(self._events.list(run.run_id)) + 1
        self._events.publish(
            WorkflowEvent(
                event_id=str(uuid4()),
                run_id=run.run_id,
                sequence=sequence,
                event_type=event_type,
                node_name=node_name,
                public_payload=public_payload or {},
                created_at=datetime.now(timezone.utc),
            )
        )

    def save_progress(
        self,
        run: WorkflowRun,
        node_name: str,
        route: RouteDecision | None = None,
        completed: bool = False,
    ) -> WorkflowRun:
        """保存一个成功节点边界，并按固定顺序追加公开事件。"""

        expected_version = run.version
        run.updated_at = datetime.now(timezone.utc)
        saved = self._run_store.save(run, expected_version=expected_version)
        checkpoints = self._checkpoints.setdefault(run.run_id, [])
        checkpoints.append(
            WorkflowCheckpoint(
                checkpoint_id=str(uuid4()),
                run_id=run.run_id,
                sequence=len(checkpoints) + 1,
                node_name=node_name,
                context=saved.context.model_copy(deep=True),
                definition_digest=saved.definition_digest,
                created_at=saved.updated_at,
            )
        )
        self.publish_event(saved, "node.succeeded", node_name=node_name)
        if route is not None:
            self.publish_event(
                saved,
                "node.routed",
                node_name=node_name,
                public_payload={
                    "target": route.target,
                    "condition": route.condition,
                    "reason_code": route.reason_code,
                    "reason_summary": route.reason_summary,
                },
            )
        if completed:
            self.publish_event(saved, "run.succeeded")
        return saved

    def list(self, run_id: str) -> list[WorkflowCheckpoint]:
        return [checkpoint.model_copy(deep=True) for checkpoint in self._checkpoints.get(run_id, [])]
