from datetime import datetime, timezone

from sqlalchemy import delete
from sqlmodel import Session, select

from common.core.db import engine
from sqlbot_platform.workflow_engine.domain.artifact import WorkflowArtifact
from sqlbot_platform.workflow_engine.domain.checkpoint import WorkflowCheckpoint
from sqlbot_platform.workflow_engine.domain.context import WorkflowContext
from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.domain.run import RunStatus
from sqlbot_platform.workflow_engine.infrastructure.persistence.artifact_repository import (
    ArtifactRepository,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.run_repository import (
    RunRepository,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.unit_of_work import (
    WorkflowUnitOfWork,
)
from tests.workflow_engine.test_run_repository import _domain_run


def _cleanup(session: Session) -> None:
    for model in (
        InteractionRequestModel,
        WorkflowArtifactModel,
        WorkflowEventModel,
        WorkflowCheckpointModel,
        NodeExecutionModel,
        WorkflowRunModel,
    ):
        session.execute(delete(model).where(model.run_id.like("repo-%")))
    session.commit()


def test_unit_of_work_advances_run_checkpoint_node_execution_and_event():
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        _cleanup(session)
        run = RunRepository(session).create(_domain_run("repo-uow"))
        session.commit()
        run.status = RunStatus.RUNNING
        run.current_node = "finish"
        run.context = WorkflowContext(request={"tenant_id": 1}, variables={"answer": "ok"})

        WorkflowUnitOfWork(session).commit_node_success(
            run=run,
            expected_version=0,
            node_name="start",
            node_type="transform",
            handler="start",
            input_summary={"question": "销售额"},
            output_summary={"answer": "ok"},
            checkpoint=WorkflowCheckpoint(
                checkpoint_id="repo-cp",
                run_id="repo-uow",
                sequence=1,
                node_name="start",
                context=run.context,
                definition_digest=run.definition_digest,
                created_at=now,
            ),
            events=[
                WorkflowEvent(
                    event_id="repo-event",
                    run_id="repo-uow",
                    sequence=1,
                    event_type="node.succeeded",
                    node_name="start",
                    created_at=now,
                )
            ],
        )
        session.commit()

        assert session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "repo-uow")).one().version == 1
        assert session.exec(select(NodeExecutionModel).where(NodeExecutionModel.run_id == "repo-uow")).one().node_name == "start"
        assert session.exec(select(WorkflowCheckpointModel).where(WorkflowCheckpointModel.run_id == "repo-uow")).one().checkpoint_id == "repo-cp"
        assert session.exec(select(WorkflowEventModel).where(WorkflowEventModel.run_id == "repo-uow")).one().event_type == "node.succeeded"
        _cleanup(session)


def test_artifact_repository_marks_temporary_artifact_as_referenced():
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        _cleanup(session)
        repo = ArtifactRepository(session)
        repo.put(
            WorkflowArtifact(
                artifact_id="repo-artifact",
                run_id="repo-artifact-run",
                kind="sql",
                content_type="text/sql",
                size=8,
                digest="sha256:sql",
                storage_uri="memory://repo-artifact",
                created_at=now,
            )
        )
        repo.mark_referenced("repo-artifact")
        session.commit()

        stored = session.exec(select(WorkflowArtifactModel).where(WorkflowArtifactModel.artifact_id == "repo-artifact")).one()
        assert stored.temporary is False
        _cleanup(session)
