from datetime import datetime, timezone
from uuid import uuid4

from sqlbot_platform.workflow_engine.domain.checkpoint import WorkflowCheckpoint
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.domain.run import WorkflowRun
from sqlbot_platform.workflow_engine.ports.event_publisher import EventPublisher
from sqlbot_platform.workflow_engine.ports.run_store import RunStore
from sqlbot_platform.workflow_engine.runtime.router import RouteDecision


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
        summary: dict | None = None,
        node_label: str | None = None,
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
        self.publish_event(
            saved,
            "node.succeeded",
            node_name=node_name,
            public_payload=self._node_public_payload(summary=summary, node_label=node_label),
        )
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

    def pause(
        self,
        run: WorkflowRun,
        node_name: str,
        summary: dict | None = None,
        node_label: str | None = None,
    ) -> WorkflowRun:
        """保存交互节点的暂停边界。"""

        saved = self.save_progress(run, node_name=node_name, summary=summary, node_label=node_label)
        self.publish_event(
            saved,
            "run.waiting_input",
            node_name=node_name,
            public_payload=self._node_public_payload(
                pending_interaction=summary,
                node_label=node_label,
            ),
        )
        return saved

    def resume(self, run: WorkflowRun) -> WorkflowRun:
        """保存服务端计算出的恢复游标，不重复创建节点 Checkpoint。"""

        expected_version = run.version
        run.updated_at = datetime.now(timezone.utc)
        saved = self._run_store.save(run, expected_version=expected_version)
        self.publish_event(saved, "run.resumed")
        return saved

    def fail(
        self,
        run: WorkflowRun,
        error_code: str,
        node_name: str | None = None,
        node_label: str | None = None,
    ) -> WorkflowRun:
        """以稳定错误码终止 Run，同时保留最近成功 Checkpoint。"""

        expected_version = run.version
        run.updated_at = datetime.now(timezone.utc)
        saved = self._run_store.save(run, expected_version=expected_version)
        payload = {"error_code": error_code}
        if node_label:
            payload["label"] = node_label
        if node_name is not None:
            self.publish_event(saved, "node.failed", node_name=node_name, public_payload=payload)
        self.publish_event(saved, "run.failed", public_payload=payload)
        return saved

    @staticmethod
    def _node_public_payload(
        *,
        summary: dict | None = None,
        pending_interaction: dict | None = None,
        node_label: str | None = None,
    ) -> dict:
        payload: dict = {}
        if node_label:
            payload["label"] = node_label
        if summary is not None:
            payload["summary"] = summary
        if pending_interaction is not None:
            payload["pending_interaction"] = pending_interaction
        return payload
