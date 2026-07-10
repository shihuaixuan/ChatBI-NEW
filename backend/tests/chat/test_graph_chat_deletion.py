"""验证会话删除与 Graph Workflow 数据使用同一生命周期。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chat.models.chat_model import Chat, ChatLog, ChatRecord
from apps.chat.services.deletion import ChatDeletionService
from apps.workflow_engine.infrastructure.artifacts.cleanup import ArtifactCleanupService
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactCleanupModel,
    WorkflowArtifactModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from common.core.db import engine


@pytest.fixture
def current_user():
    return SimpleNamespace(id=9301, oid=9401)


def _cleanup_test_data(session: Session, current_user) -> None:
    """只清理本测试用户和固定 Artifact，避免影响其他测试数据。"""

    run_ids = session.exec(
        select(WorkflowRunModel.run_id).where(WorkflowRunModel.user_id == current_user.id)
    ).all()
    if run_ids:
        session.execute(delete(NodeExecutionModel).where(NodeExecutionModel.run_id.in_(run_ids)))
        session.execute(delete(WorkflowCheckpointModel).where(WorkflowCheckpointModel.run_id.in_(run_ids)))
        session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.in_(run_ids)))
        session.execute(delete(InteractionRequestModel).where(InteractionRequestModel.run_id.in_(run_ids)))
        session.execute(delete(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(run_ids)))
        session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(run_ids)))
    record_ids = session.exec(select(ChatRecord.id).where(ChatRecord.create_by == current_user.id)).all()
    if record_ids:
        session.execute(delete(ChatLog).where(ChatLog.pid.in_(record_ids)))
        session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
    session.execute(delete(Chat).where(Chat.create_by == current_user.id))
    session.execute(
        delete(WorkflowArtifactCleanupModel).where(
            WorkflowArtifactCleanupModel.artifact_id.in_(["artifact-delete", "owned-delete-artifact"])
        )
    )
    session.commit()


@pytest.fixture
def session(current_user):
    with Session(engine) as session:
        _cleanup_test_data(session, current_user)
        yield session
        session.rollback()
        _cleanup_test_data(session, current_user)


def test_artifact_cleanup_deletes_body_and_marks_task_succeeded(session, tmp_path):
    body = tmp_path / "artifact-delete.json"
    body.write_text("{}")
    task = WorkflowArtifactCleanupModel(
        artifact_id="artifact-delete",
        storage_uri=body.as_uri(),
        status="pending",
        attempts=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()

    processed = ArtifactCleanupService(session, root=tmp_path).process_pending()

    assert processed == 1
    assert body.exists() is False
    session.refresh(task)
    assert task.status == "succeeded"
    assert task.attempts == 1
    assert task.last_error is None


def test_chat_deletion_removes_owned_workflow_data_but_keeps_standalone_run(
    session,
    current_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SQLBOT_WORKFLOW_ARTIFACT_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    body = tmp_path / "owned-delete-artifact.json"
    body.write_text("{}")
    chat = Chat(
        oid=current_user.oid,
        create_time=now.replace(tzinfo=None),
        create_by=current_user.id,
        brief="delete-owned-chat",
        chat_type="chat",
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
    )
    session.add(chat)
    session.flush()
    record = ChatRecord(
        chat_id=chat.id or 0,
        create_time=now.replace(tzinfo=None),
        create_by=current_user.id,
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
        execution_type="graph",
        question="删除测试",
        status="succeeded",
        trace_id="owned-delete-run",
        finish=True,
    )
    session.add(record)
    session.flush()
    owned_run = WorkflowRunModel(
        run_id="owned-delete-run",
        oid=current_user.oid,
        user_id=current_user.id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="delete-test",
        status="succeeded",
        chat_id=chat.id,
        record_id=record.id,
        context={},
        request={},
        output={},
        version=1,
        created_at=now,
        updated_at=now,
    )
    standalone = WorkflowRunModel(
        run_id="standalone-keep",
        oid=current_user.oid,
        user_id=current_user.id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="delete-test",
        status="succeeded",
        context={},
        request={},
        output={},
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(owned_run)
    session.add(standalone)
    session.add(
        WorkflowEventModel(
            event_id="owned-delete-event",
            run_id=owned_run.run_id,
            sequence=1,
            event_type="run.succeeded",
            public_payload={},
            internal_payload={},
            created_at=now,
        )
    )
    session.add(
        WorkflowArtifactModel(
            artifact_id="owned-delete-artifact",
            run_id=owned_run.run_id,
            kind="sql_result",
            content_type="application/json",
            size=2,
            digest="sha256:test",
            storage_uri=body.as_uri(),
            metadata_json={},
            temporary=False,
            created_at=now,
        )
    )
    session.commit()
    chat_id = chat.id or 0
    record_id = record.id or 0
    owned_run_id = owned_run.run_id
    standalone_run_id = standalone.run_id

    deleted_message = ChatDeletionService(session).delete_for_user(current_user, chat_id)
    repeated_message = ChatDeletionService(session).delete_for_user(current_user, chat_id)

    assert deleted_message == repeated_message
    assert session.get(Chat, chat_id) is None
    assert session.get(ChatRecord, record_id) is None
    assert session.exec(
        select(WorkflowRunModel).where(WorkflowRunModel.run_id == owned_run_id)
    ).one_or_none() is None
    assert session.exec(
        select(WorkflowEventModel).where(WorkflowEventModel.run_id == owned_run_id)
    ).one_or_none() is None
    assert session.exec(
        select(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id == owned_run_id)
    ).one_or_none() is None
    assert session.exec(
        select(WorkflowRunModel).where(WorkflowRunModel.run_id == standalone_run_id)
    ).one() is not None
    cleanup = session.exec(
        select(WorkflowArtifactCleanupModel).where(
            WorkflowArtifactCleanupModel.artifact_id == "owned-delete-artifact"
        )
    ).one()
    assert cleanup.status == "succeeded"
    assert body.exists() is False
