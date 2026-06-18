from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlmodel import Session

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from apps.workflow_engine.infrastructure.persistence.run_repository import (
    RunRepository,
    RunVersionConflictError,
)
from common.core.db import engine


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


def _domain_run(run_id: str = "repo-run") -> WorkflowRun:
    now = datetime.now(timezone.utc)
    return WorkflowRun(
        run_id=run_id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="sha256:def",
        status=RunStatus.CREATED,
        current_node="start",
        context=WorkflowContext(request={"tenant_id": 1, "user_id": 7}),
        created_at=now,
        updated_at=now,
    )


def test_run_repository_creates_loads_and_uses_optimistic_version():
    with Session(engine) as session:
        _cleanup(session)
        repo = RunRepository(session)
        created = repo.create(_domain_run())
        session.commit()

        loaded = repo.get("repo-run")
        loaded.current_node = "next"
        saved = repo.save(loaded, expected_version=created.version)
        session.commit()

        assert created.version == 0
        assert saved.version == 1
        assert repo.get("repo-run").current_node == "next"

        with pytest.raises(RunVersionConflictError):
            repo.save(loaded, expected_version=0)
        session.rollback()
        _cleanup(session)


def test_run_repository_returns_detached_domain_copy():
    with Session(engine) as session:
        _cleanup(session)
        repo = RunRepository(session)
        repo.create(_domain_run())
        session.commit()

        loaded = repo.get("repo-run")
        loaded.context.request["tenant_id"] = 999

        assert repo.get("repo-run").context.request["tenant_id"] == 1
        _cleanup(session)
