from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

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
    RunOwnershipConflictError,
    RunOwnershipIncompleteError,
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


def _bound_domain_run(run_id: str = "repo-bound-run") -> WorkflowRun:
    """构造带物理归属的交互式领域 Run。"""
    run = _domain_run(run_id)
    run.context.request.update({"chat_id": 100, "record_id": 200})
    return run


def _orm_run(
    *,
    chat_id: int | None,
    record_id: int | None,
    context_request: dict,
) -> WorkflowRunModel:
    """构造用于验证归属合并规则的 ORM 快照。"""
    now = datetime.now(timezone.utc)
    return WorkflowRunModel(
        run_id="repo-ownership-model",
        oid=1,
        user_id=7,
        chat_id=chat_id,
        record_id=record_id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="sha256:def",
        status=RunStatus.CREATED.value,
        current_node="start",
        context=WorkflowContext(request=context_request).model_dump(mode="json"),
        request=context_request,
        output={},
        version=0,
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


def test_run_repository_save_rejects_conflicting_ownership_before_write():
    with Session(engine) as session:
        _cleanup(session)
        repo = RunRepository(session)
        created = repo.create(_bound_domain_run())
        session.commit()
        conflicting = created.model_copy(deep=True)
        conflicting.context.request["chat_id"] = 101
        conflicting.current_node = "conflicting-node"

        with pytest.raises(
            RunOwnershipConflictError, match="GRAPH_CHAT_OWNERSHIP_CONFLICT"
        ):
            repo.save(conflicting, expected_version=created.version)

        model = session.exec(
            select(WorkflowRunModel).where(
                WorkflowRunModel.run_id == "repo-bound-run"
            )
        ).one()
        assert model.context["request"]["chat_id"] == 100
        assert model.current_node == "start"
        assert model.version == created.version
        _cleanup(session)


def test_run_repository_get_rejects_persisted_ownership_conflict():
    with Session(engine) as session:
        _cleanup(session)
        repo = RunRepository(session)
        repo.create(_bound_domain_run())
        session.commit()
        model = session.exec(
            select(WorkflowRunModel).where(
                WorkflowRunModel.run_id == "repo-bound-run"
            )
        ).one()
        context = WorkflowContext.model_validate(model.context)
        context.request["record_id"] = 201
        model.context = context.model_dump(mode="json")
        session.add(model)
        session.commit()

        with pytest.raises(
            RunOwnershipConflictError, match="GRAPH_CHAT_OWNERSHIP_CONFLICT"
        ):
            repo.get("repo-bound-run")
        _cleanup(session)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("chat_id", None),
        ("record_id", "1"),
        ("chat_id", True),
        ("record_id", 1.0),
    ],
)
def test_run_repository_to_domain_rejects_non_integer_context_ownership(
    field: str,
    invalid_value,
):
    with Session(engine) as session:
        repo = RunRepository(session)
        context_request = {"chat_id": 1, "record_id": 1}
        context_request[field] = invalid_value
        model = _orm_run(
            chat_id=1,
            record_id=1,
            context_request=context_request,
        )

        with pytest.raises(
            RunOwnershipConflictError, match="GRAPH_CHAT_OWNERSHIP_CONFLICT"
        ):
            repo.to_domain(model)


def test_run_repository_to_domain_rejects_half_bound_physical_ownership():
    with Session(engine) as session:
        repo = RunRepository(session)
        model = _orm_run(chat_id=100, record_id=None, context_request={})

        with pytest.raises(
            RunOwnershipIncompleteError, match="GRAPH_CHAT_OWNERSHIP_INCOMPLETE"
        ):
            repo.to_domain(model)


def test_run_repository_to_domain_fills_missing_context_ownership():
    with Session(engine) as session:
        repo = RunRepository(session)
        model = _orm_run(chat_id=100, record_id=200, context_request={"tenant_id": 1})

        run = repo.to_domain(model)

        assert run.context.request["chat_id"] == 100
        assert run.context.request["record_id"] == 200
