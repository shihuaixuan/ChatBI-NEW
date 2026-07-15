from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.workflow_engine.api.chat_history import (
    ChatProjectingRunStore,
    GraphChatRecordProjector,
    GraphResultNotProjectableError,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import WorkflowRunModel
from apps.workflow_engine.infrastructure.persistence.run_repository import RunRepository
from common.core.db import engine


def seed_graph_record(session: Session) -> tuple[Chat, ChatRecord]:
    """创建图执行历史投影所需的会话和占位记录。"""
    now = datetime.now()
    chat = Chat(
        oid=1,
        create_time=now,
        create_by=10,
        brief="projection-chat",
        chat_type="chat",
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
    )
    session.add(chat)
    session.flush()
    record = ChatRecord(
        chat_id=chat.id or 0,
        create_time=now,
        create_by=10,
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
        execution_type="graph",
        question="本月销售额",
        finish=False,
        status="created",
        trace_id="projection-run",
    )
    session.add(record)
    session.flush()
    return chat, record


def workflow_run(
    *,
    record_id: int | None,
    chat_id: int | None,
    status: RunStatus,
    variables: dict,
) -> WorkflowRun:
    """构造只包含投影所需字段的领域 Run。"""
    now = datetime.now(timezone.utc)
    request = {"tenant_id": 1, "user_id": 10, "dataset_id": 20}
    if chat_id is not None:
        request["chat_id"] = chat_id
    if record_id is not None:
        request["record_id"] = record_id
    return WorkflowRun(
        run_id="projection-run",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="projection-test",
        status=status,
        current_node="finish",
        context=WorkflowContext(request=request, variables=variables),
        version=1,
        created_at=now,
        updated_at=now,
    )


def _cleanup(session: Session) -> None:
    """清理当前测试使用的固定投影数据。"""
    session.execute(
        delete(WorkflowRunModel).where(WorkflowRunModel.run_id == "projection-run")
    )
    session.execute(
        delete(ChatRecord).where(
            ChatRecord.trace_id.in_(["projection-run", "projection-seed"])
        )
    )
    session.execute(delete(Chat).where(Chat.brief == "projection-chat"))
    session.commit()


@pytest.fixture
def session():
    """为每个投影用例提供独立数据库会话并回收测试数据。"""
    with Session(engine) as test_session:
        _cleanup(test_session)
        yield test_session
        test_session.rollback()
        _cleanup(test_session)


def test_projector_writes_success_snapshot(session: Session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.SUCCEEDED,
        variables={
            "final_reply": {
                "final_answer": "本月销售额为 100 元。",
                "chart": {"type": "table"},
            },
            "sql": {"sql": "select 100 as sales"},
        },
    )

    projected = GraphChatRecordProjector(session).project(run)

    assert projected is not None
    assert projected.status == "succeeded"
    assert projected.finish is True
    assert projected.sql_answer == "本月销售额为 100 元。"
    assert projected.sql == "select 100 as sales"
    assert projected.chart == '{"type":"table"}'
    assert projected.execution_type == "graph"


def test_projector_rejects_success_without_displayable_answer(session: Session):
    chat, record = seed_graph_record(session)
    original_snapshot = (
        record.trace_id,
        record.execution_type,
        record.status,
        record.finish,
        record.finish_time,
    )
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.SUCCEEDED,
        variables={"final_reply": {}},
    )

    with pytest.raises(
        GraphResultNotProjectableError, match="GRAPH_RESULT_NOT_PROJECTABLE"
    ):
        GraphChatRecordProjector(session).project(run)

    assert (
        record.trace_id,
        record.execution_type,
        record.status,
        record.finish,
        record.finish_time,
    ) == original_snapshot
    assert record not in session.dirty


def test_projector_keeps_waiting_record_recoverable(session: Session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.WAITING_INPUT,
        variables={},
    )

    projected = GraphChatRecordProjector(session).project(run)

    assert projected is not None
    assert projected.status == "waiting_input"
    assert projected.finish is False
    assert projected.trace_id == run.run_id


def test_projector_writes_failed_snapshot(session: Session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.FAILED,
        variables={},
    )

    projected = GraphChatRecordProjector(session).project(run)

    assert projected is not None
    assert projected.status == "failed"
    assert projected.finish is True
    assert projected.finish_time is not None
    assert projected.error == "GRAPH_RUN_FAILED"


def test_projector_ignores_standalone_run(session: Session):
    run = workflow_run(
        record_id=None, chat_id=None, status=RunStatus.SUCCEEDED, variables={}
    )

    assert GraphChatRecordProjector(session).project(run) is None


def test_projector_rejects_incomplete_chat_ownership(session: Session):
    run = workflow_run(
        record_id=1, chat_id=None, status=RunStatus.RUNNING, variables={}
    )

    with pytest.raises(
        GraphResultNotProjectableError, match="GRAPH_CHAT_OWNERSHIP_INCOMPLETE"
    ):
        GraphChatRecordProjector(session).project(run)


def test_projector_rejects_record_from_another_chat(session: Session):
    _, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=(record.chat_id or 0) + 1,
        status=RunStatus.RUNNING,
        variables={},
    )

    with pytest.raises(
        GraphResultNotProjectableError, match="GRAPH_CHAT_RECORD_NOT_FOUND"
    ):
        GraphChatRecordProjector(session).project(run)


def test_project_model_uses_physical_ownership_when_context_omits_it(
    session: Session,
):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.WAITING_INPUT,
        variables={},
    )
    model = WorkflowRunModel(
        run_id=run.run_id,
        oid=1,
        user_id=10,
        chat_id=chat.id,
        record_id=record.id,
        definition_name=run.definition_name,
        definition_version=run.definition_version,
        definition_digest=run.definition_digest,
        status=run.status.value,
        current_node=run.current_node,
        context=WorkflowContext(
            request={"tenant_id": 1, "user_id": 10},
            variables=run.context.variables,
        ).model_dump(mode="json"),
        request={"tenant_id": 1, "user_id": 10},
        output={},
        version=run.version,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )

    projected = GraphChatRecordProjector(session).project_model(model)

    assert projected is not None
    assert projected.status == "waiting_input"


def test_project_model_rejects_context_ownership_conflict(session: Session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.RUNNING,
        variables={},
    )
    model = WorkflowRunModel(
        run_id=run.run_id,
        oid=1,
        user_id=10,
        chat_id=chat.id,
        record_id=record.id,
        definition_name=run.definition_name,
        definition_version=run.definition_version,
        definition_digest=run.definition_digest,
        status=run.status.value,
        current_node=run.current_node,
        context=WorkflowContext(
            request={
                "tenant_id": 1,
                "user_id": 10,
                "chat_id": (chat.id or 0) + 1,
                "record_id": record.id,
            }
        ).model_dump(mode="json"),
        request={},
        output={},
        version=run.version,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )

    with pytest.raises(
        GraphResultNotProjectableError, match="GRAPH_CHAT_OWNERSHIP_CONFLICT"
    ):
        GraphChatRecordProjector(session).project_model(model)


def test_run_repository_create_persists_physical_chat_ownership(session: Session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.CREATED,
        variables={},
    )

    RunRepository(session).create(run)

    model = session.exec(
        select(WorkflowRunModel).where(WorkflowRunModel.run_id == run.run_id)
    ).one()
    assert model.chat_id == chat.id
    assert model.record_id == record.id


def test_projecting_run_store_projects_before_caller_commit(session: Session):
    chat, record = seed_graph_record(session)
    record.trace_id = "projection-seed"
    record.execution_type = "legacy"
    record.status = "seeded"
    session.flush()
    base = RunRepository(session)
    store = ChatProjectingRunStore(base, GraphChatRecordProjector(session))
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.CREATED,
        variables={},
    )
    created = store.create(run)

    assert record.trace_id == run.run_id
    assert record.execution_type == "graph"
    assert record.status == "created"
    session.commit()
    run.status = RunStatus.WAITING_INPUT

    saved = store.save(run, expected_version=run.version)

    assert store.get(run.run_id).version == created.version + 1
    assert saved.status is RunStatus.WAITING_INPUT
    assert session.get(ChatRecord, record.id).status == "waiting_input"
    session.rollback()
    session.expire_all()
    assert session.get(ChatRecord, record.id).status == "created"
