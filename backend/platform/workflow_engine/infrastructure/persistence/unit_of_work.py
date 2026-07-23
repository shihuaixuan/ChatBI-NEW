from datetime import datetime, timezone

from sqlmodel import Session

from sqlbot_platform.workflow_engine.domain.checkpoint import WorkflowCheckpoint
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.domain.run import WorkflowRun
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    NodeExecutionModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.run_repository import (
    RunRepository,
)


class WorkflowUnitOfWork:
    """节点边界事务。

    调用方负责在外层 commit/rollback；该类保证一次节点推进所需的 Run、执行记录、
    Checkpoint 和事件都写入同一个 SQLAlchemy Session。
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._runs = RunRepository(session)

    def commit_node_success(
        self,
        run: WorkflowRun,
        expected_version: int,
        node_name: str,
        node_type: str,
        handler: str,
        input_summary: dict,
        output_summary: dict,
        checkpoint: WorkflowCheckpoint,
        events: list[WorkflowEvent],
    ) -> WorkflowRun:
        saved = self._runs.save(run, expected_version=expected_version)
        now = datetime.now(timezone.utc)
        self._session.add(
            NodeExecutionModel(
                run_id=run.run_id,
                sequence=checkpoint.sequence,
                node_name=node_name,
                node_type=node_type,
                handler=handler,
                attempt=1,
                idempotency_key=f"{run.run_id}:{node_name}:1",
                status="succeeded",
                input_summary=input_summary,
                output_summary=output_summary,
                route_summary={},
                created_at=now,
                finished_at=now,
            )
        )
        self._session.add(
            WorkflowCheckpointModel(
                checkpoint_id=checkpoint.checkpoint_id,
                run_id=checkpoint.run_id,
                sequence=checkpoint.sequence,
                node_name=checkpoint.node_name,
                context=checkpoint.context.model_dump(mode="json"),
                definition_digest=checkpoint.definition_digest,
                created_at=checkpoint.created_at,
            )
        )
        for event in events:
            self._session.add(
                WorkflowEventModel(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    node_name=event.node_name,
                    node_execution_id=event.node_execution_id,
                    public_payload=event.public_payload,
                    internal_payload={},
                    internal_payload_ref=event.internal_payload_ref,
                    created_at=event.created_at,
                )
            )
        self._session.flush()
        return saved
