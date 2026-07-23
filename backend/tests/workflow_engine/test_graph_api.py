import asyncio
import copy
import importlib.util
import io
import json
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chatbi.models import Chat, ChatRecord
from apps.chatbi.orchestration.graph import runtime as chatbi_runtime
from apps.chatbi.orchestration.graph.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
)
from apps.retrieval.embedding import StaticEmbeddingProvider
from apps.retrieval.indexing import RetrievalIndexingService
from apps.retrieval.semantic_indexing import (
    SemanticIndexCoordinator,
    build_semantic_index_profile,
)
from apps.semantic.models.dto import DatasetIndexVersion
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.workflow_engine.api import router as graph_router
from apps.workflow_engine.api import service as graph_service
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from common.core.db import engine
from common.core.deps import get_current_user


async def _collect_stream_frames(stream):
    frames = []
    async for frame in stream:
        frames.append(frame)
    return "".join(frames)


@pytest.fixture(autouse=True)
def _fake_chatbi_v1_sql_execute_tool(monkeypatch):
    class FakeDatasourceQueryExecutor:
        def __init__(self, session) -> None:
            self.session = session

        def run(self, payload: dict):
            return SimpleNamespace(
                success=True,
                payload={"fields": [], "data": [{"placeholder_value": 1}], "execution_ms": 1},
                error_code=None,
                message=None,
            )

    monkeypatch.setattr(chatbi_runtime, "DatasourceQueryExecutor", FakeDatasourceQueryExecutor)


@pytest.fixture(autouse=True)
def _fake_chatbi_v1_data_policy_provider(monkeypatch):
    class FakeDataPolicyProvider:
        """Graph API 测试显式使用固定允许策略。"""

        def __init__(self, session_factory) -> None:
            self.session_factory = session_factory

        def get_policy(self, payload: dict) -> dict:
            return {
                "allowed": True,
                "row_filters": [],
                "denied_columns": [],
            }

    monkeypatch.setattr(
        chatbi_runtime,
        "SessionDataPolicyProvider",
        FakeDataPolicyProvider,
    )


@pytest.fixture(autouse=True)
def _fake_chatbi_v1_question_model(monkeypatch, tmp_path):
    class FakeQuestionModelClient:
        def __call__(self, prompt):
            user_prompt = getattr(prompt, "user_prompt", "")
            system_prompt = getattr(prompt, "system_prompt", "")
            if "rewritten_question" in system_prompt:
                if "需要澄清" in user_prompt and "sales_amount" not in user_prompt:
                    return (
                        '{"rewritten_question":"需要澄清 今日访问人数",'
                        '"need_user_input":true,"missing_slots":["metric"],"image_profile_hint":null}'
                    )
                return (
                    '{"rewritten_question":"今日访问人数","need_user_input":false,'
                    '"missing_slots":[],"image_profile_hint":null}'
                )
            if "intent_type" in system_prompt:
                return (
                    '{"intent_type":"metric_query","confidence":0.9,'
                    '"required_slot_types":["metric"],"query_shape":{"select_mode":"aggregate"},'
                    '"ambiguous_slots":[],"conflict_slots":[]}'
                )
            if "metric_mentions" in system_prompt:
                return (
                    '{"metric_mentions":["访问人数"],"time_mentions":["今日"],'
                    '"time_range":{"raw":"今日","value_status":"provided"},'
                    '"ambiguous_slots":[],"conflict_slots":[]}'
                )
            if "dimension_slots" in system_prompt:
                return (
                    '{"dimension_mentions":[],"dimension_slots":[],'
                    '"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}'
                )
            return '{"category":"data","reason":"测试模型分类为数据问题","risk_level":"low","confidence":0.9}'

    class FailingAnswerModelClient:
        def __call__(self, prompt):
            raise RuntimeError("answer model unavailable")

    def build_runtime(session, commit_events: bool = False, run_store=None):
        runtime_options = {}
        if run_store is not None:
            runtime_options["run_store"] = run_store
        return chatbi_runtime.build_real_chatbi_v1_runtime(
            session,
            question_model_client=FakeQuestionModelClient(),
            answer_model_client=FailingAnswerModelClient(),
            commit_events=commit_events,
            **runtime_options,
        )

    monkeypatch.setenv("SQLBOT_WORKFLOW_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(graph_service, "build_real_chatbi_v1_runtime", build_runtime)


def _user(user_id: int = 501, oid: int = 9501):
    return SimpleNamespace(id=user_id, oid=oid)


def _client(user=None) -> TestClient:
    app = FastAPI()
    app.include_router(graph_router.router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def _cleanup(session: Session) -> None:
    chat_ids = session.exec(
        select(Chat.id).where(
            Chat.oid == 9501,
            Chat.brief.in_(["api_graph_context_chat", "api_graph_owned_chat"]),
        )
    ).all()
    if chat_ids:
        session.execute(delete(ChatRecord).where(ChatRecord.chat_id.in_(chat_ids)))
        session.execute(delete(Chat).where(Chat.id.in_(chat_ids)))
    session.execute(delete(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.like("api-%")))
    session.execute(delete(InteractionRequestModel).where(InteractionRequestModel.run_id.like("api-%")))
    session.execute(delete(NodeExecutionModel).where(NodeExecutionModel.run_id.like("api-%")))
    session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.like("api-%")))
    session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.like("api-%")))
    _cleanup_semantic_fixture(session)
    session.commit()


def _cleanup_semantic_fixture(session: Session, oid: int = 9501) -> None:
    dataset_ids = session.exec(
        select(SemanticDataset.id).where(SemanticDataset.oid == oid, SemanticDataset.biz_name == "api_stall_dataset")
    ).all()
    model_ids = session.exec(
        select(SemanticModel.id).where(SemanticModel.oid == oid, SemanticModel.biz_name == "api_stall_traffic_model")
    ).all()
    domain_ids = session.exec(
        select(SemanticDomain.id).where(SemanticDomain.oid == oid, SemanticDomain.biz_name == "api_graph_v1_domain")
    ).all()
    if dataset_ids:
        session.execute(delete(SemanticDatasetAsset).where(SemanticDatasetAsset.dataset_id.in_(dataset_ids)))
        session.execute(delete(SemanticDatasetModelConfig).where(SemanticDatasetModelConfig.dataset_id.in_(dataset_ids)))
        session.execute(delete(SemanticDataset).where(SemanticDataset.id.in_(dataset_ids)))
    if model_ids:
        session.execute(delete(SemanticMetric).where(SemanticMetric.model_id.in_(model_ids)))
        session.execute(delete(SemanticDimension).where(SemanticDimension.model_id.in_(model_ids)))
        session.execute(delete(SemanticModel).where(SemanticModel.id.in_(model_ids)))
    if domain_ids:
        session.execute(delete(SemanticDomain).where(SemanticDomain.id.in_(domain_ids)))


def _seed_v1_semantic_dataset(session: Session, oid: int = 9501) -> int:
    _cleanup_semantic_fixture(session, oid=oid)
    domain = SemanticDomain(oid=oid, name="API 测试域", biz_name="api_graph_v1_domain")
    session.add(domain)
    session.flush()

    model = SemanticModel(
        oid=oid,
        domain_id=domain.id or 0,
        datasource_id=7001,
        name="店铺流量模型",
        biz_name="api_stall_traffic_model",
        source_type="TABLE",
        table_name="stall_traffic_daily",
        model_detail={
            "queryType": "table_query",
            "tableQuery": {"table": "stall_traffic_daily"},
            "fields": [
                {"fieldName": "visit_uv", "dataType": "BIGINT"},
                {"fieldName": "stat_date", "dataType": "DATE"},
            ],
            "dimensions": [
                {
                    "name": "统计日期",
                    "bizName": "stat_date",
                    "expr": "stat_date",
                    "type": "partition_time",
                }
            ],
            "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
        },
    )
    session.add(model)
    session.flush()

    metric = SemanticMetric(
        oid=oid,
        model_id=model.id or 0,
        name="访问人数",
        biz_name="visit_uv",
        alias=["访客数"],
        description="店铺访问人数 UV",
        default_agg="SUM",
        fields=["visit_uv"],
    )
    dimension = SemanticDimension(
        oid=oid,
        model_id=model.id or 0,
        name="档口",
        biz_name="stall_id",
        description="店铺档口维度",
    )
    time_dimension = SemanticDimension(
        oid=oid,
        model_id=model.id or 0,
        name="统计日期",
        biz_name="stat_date",
        type="partition_time",
        semantic_type="time",
        expr="stat_date",
        field_name="stat_date",
        is_default_time=True,
        time_granularities=["day"],
    )
    session.add(metric)
    session.add(dimension)
    session.add(time_dimension)
    session.flush()

    dataset = SemanticDataset(
        oid=oid,
        domain_id=domain.id or 0,
        name="API 店铺数据集",
        biz_name="api_stall_dataset",
        data_set_detail={
            "dataSetModelConfigs": [
                {
                    "id": model.id,
                    "includesAll": False,
                    "metrics": [metric.id],
                    "dimensions": [dimension.id, time_dimension.id],
                }
            ]
        },
    )
    session.add(dataset)
    session.flush()
    profile = build_semantic_index_profile()
    schema = SemanticSchemaService(SemanticSchemaLoader(session)).build_dataset_schema(
        oid, dataset.id or 0
    )
    queued = SemanticIndexCoordinator(session, profile).enqueue_dataset_rebuild(
        tenant_id=oid,
        version=DatasetIndexVersion(
            dataset_id=dataset.id or 0,
            schema_version=dataset.schema_version,
            index_version=dataset.index_version,
        ),
        schema=schema,
    )
    provider = StaticEmbeddingProvider(
        vector=[0.0] * profile.dimension,
        provider=profile.provider,
        model=profile.model,
    )
    indexing = RetrievalIndexingService(session, profile)
    for job_id in queued.job_ids:
        indexing.process_job(job_id, provider)
    session.commit()
    return dataset.id or 0


def _seed_graph_chat() -> tuple[int, int]:
    now = datetime.now()
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)
        chat = Chat(
            oid=9501,
            create_time=now,
            create_by=501,
            brief="api_graph_owned_chat",
            chat_type="chat",
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)
        return chat.id or 0, dataset_id


def _load_pending_interaction_id(run_id: str) -> str:
    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == run_id,
                InteractionRequestModel.status == "pending",
            )
        ).one()
        return interaction.interaction_id


def _load_chat_record(record_id: int) -> ChatRecord:
    with Session(engine) as session:
        record = session.get(ChatRecord, record_id)
        assert record is not None
        session.expunge(record)
        return record


def test_graph_routes_are_registered_and_included_by_apps_api():
    from apps.api import api_router

    app = FastAPI()
    app.include_router(graph_router.router)

    paths = app.openapi()["paths"]

    expected_routes = {
        "/graph/queries",
        "/graph/queries/stream",
        "/graph/chats/{chat_id}/queries",
        "/graph/chats/{chat_id}/queries/stream",
        "/graph/runs/{run_id}",
        "/graph/runs/{run_id}/events",
        "/graph/runs/{run_id}/events/stream",
        "/graph/runs/{run_id}/trace",
        "/graph/runs/{run_id}/interactions/{interaction_id}/responses",
        "/graph/runs/{run_id}/interactions/{interaction_id}/responses/stream",
        "/graph/runs/{run_id}/cancel",
        "/graph/runs/{run_id}/retry",
    }
    assert expected_routes.issubset(paths.keys())

    api_py = Path(__file__).parents[2] / "apps" / "api.py"
    source = api_py.read_text()
    assert "from apps.workflow_engine.api import router as graph_workflow" in source
    assert "graph_router=graph_workflow.router" in source
    assert "api_router.include_router(chatbi_router)" in source
    assert "api_router.include_router(agent.router)" not in source
    assert "api_router.include_router(graph_workflow.router)" not in source

    registered_paths = {
        route.path for route in api_router.routes if isinstance(route, APIRoute)
    }
    assert {
        "/chat/list",
        "/chat/agent/stream",
        "/graph/queries",
    }.issubset(registered_paths)


def test_graph_routes_require_current_user_dependency():
    route_params = {
        route.path: signature(route.endpoint).parameters
        for route in graph_router.router.routes
        if isinstance(route, APIRoute)
    }

    assert route_params
    assert all("current_user" in params for params in route_params.values())


def test_graph_query_creates_run_and_executes_placeholder_chatbi_graph():
    with Session(engine) as session:
        _cleanup(session)

    response = _client().post(
        "/graph/queries",
        json={
            "question": "最近 7 天销售额",
            "dataset_id": 7001,
            "request_id": "api-request-1",
            "run_id": "api-run-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run-1"
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    assert body["context_summary"]["variables"]["answer"]["answer"] == "这是图工作流占位回答：最近 7 天销售额"

    with Session(engine) as session:
        stored = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-run-1")).one()
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-1")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        assert stored.oid == 9501
        assert stored.user_id == 501
        assert stored.chat_id is None
        assert stored.record_id is None
        assert stored.request == {
            "tenant_id": 9501,
            "user_id": 501,
            "question": "最近 7 天销售额",
            "dataset_id": 7001,
            "source_dataset_id": 7001,
            "datasource_id": 7001,
            "request_id": "api-request-1",
        }
        event_types = [event.event_type for event in events]
        assert event_types[0] == "run.created"
        assert "node.started" in event_types
        assert "node.succeeded" in event_types
        assert event_types[-1] == "run.succeeded"
        assert events[0].public_payload == {"status": "created", "question": "最近 7 天销售额"}
        _cleanup(session)


def test_graph_chat_history_survives_reload_boundary():
    chat_id, dataset_id = _seed_graph_chat()

    response = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "本月销售额",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-owned-run",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record_id"] is not None

    with Session(engine) as session:
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-owned-run")).one()
        record = session.get(ChatRecord, body["record_id"])
        assert record is not None
        assert run.chat_id == chat_id
        assert run.record_id == record.id
        assert record.trace_id == run.run_id
        assert record.execution_type == "graph"
        assert record.status == "succeeded"
        assert record.finish is True
        assert record.sql_answer
        _cleanup(session)


def test_standalone_graph_query_rejects_body_chat_id():
    chat_id, dataset_id = _seed_graph_chat()

    response = _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-standalone-body-chat-run",
            "chat_id": chat_id,
        },
    )

    assert response.status_code == 422
    with Session(engine) as session:
        assert (
            session.exec(
            select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-standalone-body-chat-run")
            ).one_or_none()
            is None
        )
        assert (
            session.exec(select(ChatRecord).where(ChatRecord.trace_id == "api-standalone-body-chat-run")).one_or_none()
            is None
        )
        _cleanup(session)


def test_graph_chat_query_rejects_unowned_chat_without_creating_history():
    chat_id, dataset_id = _seed_graph_chat()

    response = _client(user=_user(user_id=502)).post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "本月销售额",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-unowned-run",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "CHAT_NOT_FOUND"
    with Session(engine) as session:
        assert (
            session.exec(
                select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-unowned-run")
            ).one_or_none()
            is None
        )
        assert (
            session.exec(select(ChatRecord).where(ChatRecord.trace_id == "api-chat-unowned-run")).one_or_none() is None
        )
        _cleanup(session)


def test_graph_chat_query_rejects_dataset_mismatch_without_creating_history():
    chat_id, _ = _seed_graph_chat()

    response = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "本月销售额",
            "dataset_id": 999999,
            "definition_version": "v1",
            "run_id": "api-chat-dataset-mismatch-run",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "CHAT_DATASET_MISMATCH"
    with Session(engine) as session:
        assert (
            session.exec(
                select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-dataset-mismatch-run")
            ).one_or_none()
            is None
        )
        assert (
            session.exec(select(ChatRecord).where(ChatRecord.trace_id == "api-chat-dataset-mismatch-run")).one_or_none()
            is None
        )
        _cleanup(session)


def test_graph_chat_stream_rejects_unowned_chat_before_starting_sse():
    chat_id, dataset_id = _seed_graph_chat()

    response = _client(user=_user(user_id=502)).post(
        f"/graph/chats/{chat_id}/queries/stream",
        json={
            "question": "本月销售额",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-stream-unowned-run",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "CHAT_NOT_FOUND"
    with Session(engine) as session:
        assert (
            session.exec(
                select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-stream-unowned-run")
            ).one_or_none()
            is None
        )
        _cleanup(session)


def test_graph_chat_stream_rejects_dataset_mismatch_before_starting_sse():
    chat_id, _ = _seed_graph_chat()

    response = _client().post(
        f"/graph/chats/{chat_id}/queries/stream",
        json={
            "question": "本月销售额",
            "dataset_id": 999999,
            "definition_version": "v1",
            "run_id": "api-chat-stream-dataset-mismatch-run",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "CHAT_DATASET_MISMATCH"
    with Session(engine) as session:
        assert (
            session.exec(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.run_id == "api-chat-stream-dataset-mismatch-run"
                )
            ).one_or_none()
            is None
        )
        _cleanup(session)


def test_graph_chat_query_stream_creates_owned_record_and_run():
    chat_id, dataset_id = _seed_graph_chat()

    with _client().stream(
        "POST",
        f"/graph/chats/{chat_id}/queries/stream",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-owned-stream-run",
        },
    ) as response:
        text = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: run.created\n" in text
    assert "event: run.succeeded\n" in text
    with Session(engine) as session:
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-owned-stream-run")).one()
        assert run.chat_id == chat_id
        assert run.record_id is not None
        record = session.get(ChatRecord, run.record_id)
        assert record is not None
        assert record.trace_id == run.run_id
        assert record.execution_type == "graph"
        _cleanup(session)


def test_stream_drains_final_event_after_worker_finishes(monkeypatch):
    """Worker 结束后必须再读取一次，避免终态 Run 先于最终事件可见。"""

    now = datetime.now(timezone.utc)
    run_id = "api-stream-terminal-drain"
    with Session(engine) as session:
        _cleanup(session)
        session.add(
            WorkflowRunModel(
                run_id=run_id,
                oid=9501,
                user_id=501,
                definition_name="chatbi",
                definition_version="v1",
                definition_digest="stream-test",
                status="succeeded",
                context={},
                request={},
                output={},
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

        class SequencedEventStream:
            def __init__(self, _session):
                pass

            def list(self, run_id: str, after_sequence: int = 0):
                if after_sequence == 0:
                    return [
                        WorkflowEvent(
                            event_id="node-finished",
                            run_id=run_id,
                            sequence=1,
                            event_type="node.succeeded",
                            node_name="finish",
                            created_at=now,
                        )
                    ]
                if after_sequence == 1:
                    return [
                        WorkflowEvent(
                            event_id="run-finished",
                            run_id=run_id,
                            sequence=2,
                            event_type="run.succeeded",
                            created_at=now,
                        )
                    ]
                return []

        class FinishedWorker:
            @staticmethod
            def is_alive() -> bool:
                return False

        monkeypatch.setattr(graph_service, "EventStream", SequencedEventStream)
        frames = asyncio.run(
            _collect_stream_frames(
                graph_service.GraphApiService(session)._stream_run_events(
                    _user(),
                    run_id,
                    worker=FinishedWorker(),
                    errors=[],
                )
            )
        )

        assert "event: node.succeeded\n" in frames
        assert "event: run.succeeded\n" in frames
        _cleanup(session)


def test_graph_chat_waiting_record_resumes_into_stable_snapshot():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-clarify",
        },
    )
    assert created.status_code == 200
    record_id = created.json()["record_id"]
    assert _load_chat_record(record_id).status == "waiting_user"
    interaction_id = _load_pending_interaction_id("api-chat-clarify")

    answered = _client().post(
        f"/graph/runs/api-chat-clarify/interactions/{interaction_id}/responses",
        json={"response": {"metric": "sales_amount"}},
    )

    assert answered.status_code == 200
    record = _load_chat_record(record_id)
    assert record.id == record_id
    assert record.status == "succeeded"
    assert record.finish is True
    assert record.sql_answer
    with Session(engine) as session:
        _cleanup(session)


def test_graph_chat_cancel_projects_cancelled_status():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-cancel",
        },
    )
    assert created.status_code == 200
    record_id = created.json()["record_id"]

    cancelled = _client().post("/graph/runs/api-chat-cancel/cancel")

    assert cancelled.status_code == 200
    record = _load_chat_record(record_id)
    assert record.id == record_id
    assert record.status == "cancelled"
    assert record.finish is True
    with Session(engine) as session:
        _cleanup(session)


def test_graph_chat_retry_reuses_record_and_clears_error():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-retry",
        },
    )
    assert created.status_code == 200
    record_id = created.json()["record_id"]
    with Session(engine) as session:
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-retry")).one()
        record = session.get(ChatRecord, record_id)
        assert record is not None
        run.status = "failed"
        record.status = "failed"
        record.finish = True
        record.error = "OLD_ERROR"
        session.add(run)
        session.add(record)
        session.commit()

    retried = _client().post("/graph/runs/api-chat-retry/retry")

    assert retried.status_code == 200
    record = _load_chat_record(record_id)
    assert record.id == record_id
    assert record.status == "succeeded"
    assert record.error is None
    with Session(engine) as session:
        _cleanup(session)


def test_graph_event_stream_returns_sse_frames_and_closes_for_completed_run():
    with Session(engine) as session:
        _cleanup(session)


def test_graph_query_stream_creates_run_and_streams_execution_events():
    with Session(engine) as session:
        _cleanup(session)

    with _client().stream(
        "POST",
        "/graph/queries/stream",
        json={
            "question": "最近 7 天销售额",
            "dataset_id": 7001,
            "request_id": "api-query-stream-request-1",
            "run_id": "api-query-stream-run-1",
        },
    ) as response:
        text = response.read().decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.created\n" in text
    assert "event: node.started\n" in text
    assert "event: node.succeeded\n" in text
    assert '"summary"' in text
    assert "event: run.succeeded\n" in text

    run_response = _client().get("/graph/runs/api-query-stream-run-1")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "succeeded"

    with Session(engine) as session:
        _cleanup(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "最近 7 天销售额",
            "dataset_id": 7001,
            "request_id": "api-stream-request-1",
            "run_id": "api-stream-run-1",
        },
    )

    with _client().stream(
        "GET",
        "/graph/runs/api-stream-run-1/events/stream",
        params={"after_sequence": 0},
    ) as response:
        text = response.read().decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" in text
    assert "event: run.created\n" in text
    assert "event: node.started\n" in text
    assert "event: run.succeeded\n" in text
    assert '"event_type":"run.succeeded"' in text

    with Session(engine) as session:
        _cleanup(session)


def test_graph_query_stream_waiting_input_event_contains_pending_interaction():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    with _client().stream(
        "POST",
        "/graph/queries/stream",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-query-stream-request-waiting",
            "run_id": "api-query-stream-run-waiting",
        },
    ) as response:
        text = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: run.waiting_input\n" in text
    assert '"pending_interaction"' in text
    assert '"interaction_id"' in text
    assert '"status":"pending"' in text
    assert '"node_name":"ask_rewrite_clarification"' in text
    assert "请补充要分析的指标" in text

    run_response = _client().get("/graph/runs/api-query-stream-run-waiting")
    assert run_response.status_code == 200
    pending_interaction = run_response.json()["context_summary"]["pending_interaction"]
    assert pending_interaction["status"] == "pending"
    assert pending_interaction["node_name"] == "ask_rewrite_clarification"

    with Session(engine) as session:
        _cleanup(session)


def test_stream_run_events_waits_for_resume_worker_before_closing_on_old_waiting_state():
    run_id = "api-stream-race-waiting"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        _cleanup(session)
        session.add(
            WorkflowRunModel(
                run_id=run_id,
                oid=9501,
                user_id=501,
                request_id="api-stream-race-request",
                definition_name="chatbi",
                definition_version="v1",
                definition_digest="test",
                status="waiting_input",
                current_node="ask_slot_clarification",
                context={},
                request={},
                output={},
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WorkflowEventModel(
                event_id="api-stream-race-event-1",
                run_id=run_id,
                sequence=1,
                event_type="run.waiting_input",
                node_name="ask_slot_clarification",
                public_payload={},
                internal_payload={},
                created_at=now,
            )
        )
        session.commit()

    def resume_later():
        time.sleep(0.1)
        with Session(engine) as worker_session:
            run = worker_session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one()
            run.status = "succeeded"
            run.current_node = "finish"
            run.updated_at = datetime.now(timezone.utc)
            worker_session.add(run)
            worker_session.add(
                WorkflowEventModel(
                    event_id="api-stream-race-event-2",
                    run_id=run_id,
                    sequence=2,
                    event_type="run.resumed",
                    public_payload={},
                    internal_payload={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            worker_session.add(
                WorkflowEventModel(
                    event_id="api-stream-race-event-3",
                    run_id=run_id,
                    sequence=3,
                    event_type="node.started",
                    node_name="retrieve_knowledge",
                    public_payload={},
                    internal_payload={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            worker_session.add(
                WorkflowEventModel(
                    event_id="api-stream-race-event-4",
                    run_id=run_id,
                    sequence=4,
                    event_type="run.succeeded",
                    public_payload={},
                    internal_payload={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            worker_session.commit()

    worker = threading.Thread(target=resume_later)
    worker.start()
    with Session(engine) as session:
        text = asyncio.run(
            _collect_stream_frames(
                graph_service.GraphApiService(session)._stream_run_events(
                    _user(),
                    run_id,
                    after_sequence=1,
                    worker=worker,
                )
            )
        )
    worker.join(timeout=1)

    assert "event: run.resumed\n" in text
    assert "event: node.started\n" in text
    assert '"node_name":"retrieve_knowledge"' in text

    with Session(engine) as session:
        _cleanup(session)


def test_graph_query_can_execute_chatbi_v1_graph():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    response = _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1",
            "run_id": "api-run-v1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run-v1"
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    assert body["context_summary"]["variables"]["knowledge"]["metrics"] == ["visit_uv"]
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        assert "classify_question" in [event.node_name for event in events]
        execute_event = next(
            event
            for event in events
            if event.event_type == "node.succeeded"
            and event.node_name == "execute_sql"
        )
        public_summary = json.dumps(
            execute_event.public_payload,
            ensure_ascii=False,
        )
        assert "select " not in public_summary.lower()
        assert execute_event.public_payload["summary"]["results"][0]["sample_rows"] == [
            {"placeholder_value": 1}
        ]
        _cleanup(session)


def test_graph_chat_query_loads_previous_semantic_context():
    now = datetime.now()
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)
        chat = Chat(
            oid=9501,
            create_time=now,
            create_by=501,
            brief="api_graph_context_chat",
            chat_type="chat",
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.flush()
        previous_record = ChatRecord(
            chat_id=chat.id or 0,
            create_time=now,
            finish_time=now,
            create_by=501,
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
            execution_type="graph",
            question="今天店铺的访问人数",
            finish=True,
            status="succeeded",
            trace_id="api-prev-context",
        )
        session.add(previous_record)
        session.flush()
        session.add(
            WorkflowRunModel(
                run_id="api-prev-context",
                oid=9501,
                user_id=501,
                request_id="api-prev-context-request",
                definition_name="chatbi",
                definition_version="v1",
                definition_digest="test",
                status="succeeded",
                current_node="finish",
                chat_id=chat.id,
                record_id=previous_record.id,
                context={
                    "request": {
                        "tenant_id": 9501,
                        "user_id": 501,
                        "question": "今天店铺的访问人数",
                        "dataset_id": dataset_id,
                    },
                    "conversation": {"question": "今天店铺的访问人数"},
                    "variables": {
                        "rewrite": {"rewritten_question": "查询今天店铺的访问人数"},
                        "intent": {
                            "intent_type": "metric_query",
                            "metric_mentions": ["访问人数"],
                            "time_range": {"raw": "今天", "value_status": "provided"},
                            "dimension_slots": [{"name": "店铺", "role": "ambiguous"}],
                            "filter_mentions": [],
                            "query_shape": {"select_mode": "aggregate"},
                        },
                    },
                },
                request={},
                output={},
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        legacy_record = ChatRecord(
            chat_id=chat.id or 0,
            create_time=now + timedelta(seconds=1),
            finish_time=now + timedelta(seconds=1),
            create_by=501,
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
            execution_type="legacy",
            question="不应进入 Graph 上下文的传统问题",
            finish=True,
            status="succeeded",
            trace_id="api-legacy-context",
        )
        session.add(legacy_record)
        session.flush()
        session.add(
            WorkflowRunModel(
                run_id="api-legacy-context",
                oid=9501,
                user_id=501,
                definition_name="chatbi",
                definition_version="v1",
                definition_digest="test",
                status="succeeded",
                chat_id=chat.id,
                record_id=legacy_record.id,
                context={
                    "request": {"dataset_id": dataset_id},
                    "variables": {"intent": {"metric_mentions": ["传统指标"]}},
                },
                request={"dataset_id": dataset_id},
                output={},
                version=1,
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )
        failed_record = ChatRecord(
            chat_id=chat.id or 0,
            create_time=now + timedelta(seconds=2),
            finish_time=now + timedelta(seconds=2),
            create_by=501,
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
            execution_type="graph",
            question="失败的后续问题",
            finish=True,
            status="failed",
            trace_id="api-failed-context",
        )
        session.add(failed_record)
        session.flush()
        session.add(
            WorkflowRunModel(
                run_id="api-failed-context",
                oid=9501,
                user_id=501,
                definition_name="chatbi",
                definition_version="v1",
                definition_digest="test",
                status="failed",
                chat_id=chat.id,
                record_id=failed_record.id,
                context={"request": {"dataset_id": dataset_id}},
                request={"dataset_id": dataset_id},
                output={},
                version=1,
                created_at=now + timedelta(seconds=2),
                updated_at=now + timedelta(seconds=2),
            )
        )
        session.commit()
        chat_id = chat.id

    response = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "那订单数呢",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-context-request",
            "run_id": "api-context-run",
        },
    )

    assert response.status_code == 200

    with Session(engine) as session:
        stored = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-context-run")).one()
        record = session.exec(select(ChatRecord).where(ChatRecord.trace_id == "api-context-run")).one()
        context = stored.context
        assert stored.request["chat_id"] == chat_id
        assert stored.request["record_id"] == record.id
        assert record.question == "那订单数呢"
        assert record.status == stored.status
        assert record.finish is True
        assert context["conversation"]["question"] == "那订单数呢"
        assert context["conversation"]["last_question"] == "今天店铺的访问人数"
        assert context["conversation"]["last_rewritten_question"] == "查询今天店铺的访问人数"
        assert context["conversation"]["last_intent"]["metric_mentions"] == ["访问人数"]
        assert context["conversation"]["last_intent"]["time_range"] == {
            "raw": "今天",
            "value_status": "provided",
        }
        _cleanup(session)


def test_graph_v1_classification_model_failure_degrades_to_explanatory_answer(monkeypatch):
    class FailingQuestionModelClient:
        def __call__(self, prompt):
            raise RuntimeError("model unavailable")

    def build_runtime(session, commit_events: bool = False):
        return chatbi_runtime.build_real_chatbi_v1_runtime(
            session,
            question_model_client=FailingQuestionModelClient(),
            answer_model_client=lambda prompt: (_ for _ in ()).throw(
                RuntimeError("answer model unavailable")
            ),
            commit_events=commit_events,
        )

    monkeypatch.setattr(graph_service, "build_real_chatbi_v1_runtime", build_runtime)

    with Session(engine) as session:
        _cleanup(session)

    response = _client().post(
        "/graph/queries",
        json={
            "question": "你好",
            "dataset_id": 3,
            "definition_version": "v1",
            "request_id": "api-request-v1-classify-failed",
            "run_id": "api-run-v1-classify-failed",
        },
    )

    assert response.status_code == 200
    body = response.json()
    # A3 降级语义：分类模型失败不再终止 Run，而是路由到解释性回答并正常完成。
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    variables = body["context_summary"]["variables"]
    assert variables["node_failure"]["error_code"] == "QUESTION_CLASSIFY_FAILED"
    assert variables["final_reply"]["final_answer"]

    with Session(engine) as session:
        executions = session.exec(
            select(NodeExecutionModel)
            .where(NodeExecutionModel.run_id == "api-run-v1-classify-failed")
            .order_by(NodeExecutionModel.sequence)
        ).all()
        assert executions[0].node_name == "classify_question"
        assert executions[0].status == "succeeded"
        assert [execution.node_name for execution in executions][-1] == "finish"
        _cleanup(session)


def test_graph_v1_interaction_response_resumes_runtime_to_final_reply():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    created = _client().post(
        "/graph/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-clarify",
            "run_id": "api-run-v1-clarify",
        },
    )

    assert created.status_code == 200
    assert created.json()["status"] == "waiting_input"
    assert created.json()["current_node"] == "ask_rewrite_clarification"

    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == "api-run-v1-clarify",
                InteractionRequestModel.status == "pending",
            )
        ).one()
        interaction_id = interaction.interaction_id

    answered = _client().post(
        f"/graph/runs/api-run-v1-clarify/interactions/{interaction_id}/responses",
        json={"response": {"metric": "sales_amount"}},
    )
    run_response = _client().get("/graph/runs/api-run-v1-clarify")

    assert answered.status_code == 200
    assert answered.json()["status"] == "succeeded"
    assert run_response.status_code == 200
    body = run_response.json()
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    assert body["context_summary"]["variables"]["rewrite_response"] == {"metric": "sales_amount"}
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == interaction_id)
        ).one()
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1-clarify")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        rewrite_executions = session.exec(
            select(NodeExecutionModel)
            .where(
                NodeExecutionModel.run_id == "api-run-v1-clarify",
                NodeExecutionModel.node_name == "rewrite_question",
            )
            .order_by(NodeExecutionModel.attempt)
        ).all()
        assert interaction.status == "answered"
        assert [execution.attempt for execution in rewrite_executions] == [1, 2]
        assert events[-1].event_type == "run.succeeded"
        _cleanup(session)


def test_graph_trace_returns_node_status_route_reason_and_outputs():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-trace",
            "run_id": "api-run-v1-trace",
        },
    )

    response = _client().get("/graph/runs/api-run-v1-trace/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run-v1-trace"
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    nodes = {node["name"]: node for node in body["nodes"]}
    assert nodes["classify_question"]["status"] == "succeeded"
    assert nodes["classify_question"]["route_reason"] == "QUESTION_DATA_OR_FOLLOWUP"
    assert nodes["classify_question"]["output"]["category"] == "data"
    assert nodes["reject_answer"]["status"] == "not_run"
    assert nodes["reject_answer"]["output"] is None
    assert nodes["generate_sql"]["output"]["statement_type"] == "select"
    assert nodes["generate_sql"]["output"]["sql"].lower().startswith("select ")
    assert nodes["generate_sql"]["output"]["artifact_ref"] is None
    assert {
        key: nodes["execute_sql"]["output"][key]
        for key in (
            "status",
            "query_count",
            "row_count",
            "fields",
            "execution_ms",
        )
    } == {
        "status": "succeeded",
        "query_count": 1,
        "row_count": 1,
        "fields": [],
        "execution_ms": 1,
    }
    assert len(nodes["execute_sql"]["output"]["artifact_refs"]) == 1
    assert nodes["execute_sql"]["output"]["artifact_refs"][0]["artifact_id"].startswith(
        "artifact-"
    )
    assert nodes["execute_sql"]["output"]["results"][0]["sample_rows"] == [{"placeholder_value": 1}]
    assert nodes["compose_final_reply"]["output"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        _cleanup(session)


def test_graph_trace_is_derived_from_v1_node_metadata():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-trace-metadata",
            "run_id": "api-run-v1-trace-metadata",
        },
    )

    response = _client().get("/graph/runs/api-run-v1-trace-metadata/trace")

    assert response.status_code == 200
    body = response.json()
    node_names = [node["name"] for node in body["nodes"]]
    assert "ask_cross_model_split" in node_names
    assert "generate_split_queries" in node_names
    assert "execute_split_queries" in node_names
    assert "validate_result" in node_names
    nodes = {node["name"]: node for node in body["nodes"]}
    assert nodes["classify_question"]["label"] == "问题分类"
    assert nodes["ask_cross_model_split"]["label"] == "确认跨模型拆分"
    assert nodes["validate_result"]["label"] == "结果校验"
    assert nodes["generate_sql"]["output"]["statement_type"] == "select"
    assert nodes["generate_sql"]["output"]["sql"].lower().startswith("select ")

    with Session(engine) as session:
        _cleanup(session)


def test_graph_node_events_use_v1_metadata_projection_for_public_summary():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-event-metadata",
            "run_id": "api-run-v1-event-metadata",
        },
    )

    with Session(engine) as session:
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1-event-metadata")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        generate_sql_event = next(
            event
            for event in events
            if event.event_type == "node.succeeded"
            and event.node_name == "generate_sql"
        )
        assert generate_sql_event.public_payload == {
            "label": "生成查询",
            "summary": {
                "statement_type": "select",
                "sql": generate_sql_event.public_payload["summary"]["sql"],
                "artifact_ref": None,
            },
        }
        assert generate_sql_event.public_payload["summary"]["sql"].lower().startswith("select ")
        _cleanup(session)


def test_graph_trace_sanitizes_unified_split_execution_results():
    with Session(engine) as session:
        service = graph_service.GraphApiService(session)
        output = service._sanitize_trace_output(
            build_chatbi_v1_definition().nodes["execute_split_queries"],
            {
                "status": "succeeded",
                "row_count": 3,
                "fields": ["value"],
                "execution_ms": 8,
                "results": [
                    {
                        "query_id": "query-0",
                        "status": "succeeded",
                        "row_count": 1,
                        "fields": ["value"],
                        "sample_rows": [{"value": 1}],
                        "artifact_ref": {"artifact_id": "artifact-1"},
                    },
                    {
                        "query_id": "query-1",
                        "status": "succeeded",
                        "row_count": 2,
                        "fields": ["value"],
                        "sample_rows": [{"value": 2}],
                        "artifact_ref": {"artifact_id": "artifact-2"},
                    },
                ],
            },
        )

    assert output == {
        "status": "succeeded",
        "query_count": 2,
        "row_count": 3,
        "fields": ["value"],
        "execution_ms": 8,
        "artifact_refs": [
            {"artifact_id": "artifact-1"},
            {"artifact_id": "artifact-2"},
        ],
        "results": [
            {
                "query_id": "query-0",
                "status": "succeeded",
                "row_count": 1,
                "fields": ["value"],
                "sample_rows": [{"value": 1}],
                "result_truncated": False,
                "execution_ms": 0,
                "artifact_ref": {"artifact_id": "artifact-1"},
            },
            {
                "query_id": "query-1",
                "status": "succeeded",
                "row_count": 2,
                "fields": ["value"],
                "sample_rows": [{"value": 2}],
                "result_truncated": False,
                "execution_ms": 0,
                "artifact_ref": {"artifact_id": "artifact-2"},
            },
        ],
    }


def test_graph_query_persists_node_execution_summaries_for_trace_and_retry():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    response = _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-node-execution",
            "run_id": "api-run-v1-node-execution",
        },
    )

    assert response.status_code == 200

    with Session(engine) as session:
        executions = session.exec(
            select(NodeExecutionModel)
            .where(NodeExecutionModel.run_id == "api-run-v1-node-execution")
            .order_by(NodeExecutionModel.sequence)
        ).all()

        assert [execution.node_name for execution in executions] == [
            "classify_question",
            "rewrite_question",
            "draw_image_profile",
            "recognize_intent",
            "retrieve_knowledge",
            "bind_query_plan",
            "generate_sql",
            "execute_sql",
            "validate_result",
            "generate_question_answer",
            "recommend_questions",
            "compose_final_reply",
            "finish",
        ]
        classify = executions[0]
        assert classify.status == "succeeded"
        assert classify.node_type == "capability"
        assert classify.handler == "question.classify"
        assert classify.input_summary == {"question": "今日访问人数", "dataset_id": dataset_id}
        assert classify.output_summary == {
            "category": "data",
            "reason": "测试模型分类为数据问题",
            "risk_level": "low",
            "confidence": 0.9,
        }
        assert classify.route_summary["reason_code"] == "QUESTION_DATA_OR_FOLLOWUP"
        _cleanup(session)


def test_graph_v1_cancelled_waiting_run_cannot_resume_from_interaction():
    with Session(engine) as session:
        _cleanup(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "需要澄清的问题",
            "dataset_id": 7001,
            "definition_version": "v1",
            "request_id": "api-request-v1-cancel",
            "run_id": "api-run-v1-cancel",
        },
    )
    with Session(engine) as session:
        interaction_id = session.exec(
            select(InteractionRequestModel.interaction_id).where(
                InteractionRequestModel.run_id == "api-run-v1-cancel",
                InteractionRequestModel.status == "pending",
            )
        ).one()

    cancel = _client().post("/graph/runs/api-run-v1-cancel/cancel")
    answer = _client().post(
        f"/graph/runs/api-run-v1-cancel/interactions/{interaction_id}/responses",
        json={"response": {"metric": "sales_amount"}},
    )
    run_response = _client().get("/graph/runs/api-run-v1-cancel")

    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert answer.status_code == 409
    assert answer.json()["detail"] == "RUN_NOT_WAITING_INPUT"
    assert run_response.json()["status"] == "cancelled"

    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == interaction_id)
        ).one()
        assert interaction.status == "cancelled"
        _cleanup(session)


def test_graph_v1_retry_resumes_from_latest_context_without_clearing_variables():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "request_id": "api-request-v1-retry",
            "run_id": "api-run-v1-retry",
        },
    )

    with Session(engine) as session:
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-run-v1-retry")).one()
        run.status = "failed"
        run.current_node = "generate_question_answer"
        context = copy.deepcopy(run.context)
        context["control"]["current_node"] = "generate_question_answer"
        context["control"]["previous_node"] = "validate_result"
        context["variables"]["retry_marker"] = "preserve-me"
        run.context = context
        run.error_code = "TEST_FAILURE"
        session.add(run)
        session.commit()

    retry = _client().post("/graph/runs/api-run-v1-retry/retry")
    run_response = _client().get("/graph/runs/api-run-v1-retry")

    assert retry.status_code == 200
    assert retry.json()["status"] == "succeeded"
    body = run_response.json()
    assert body["status"] == "succeeded"
    assert body["current_node"] == "finish"
    assert body["context_summary"]["variables"]["retry_marker"] == "preserve-me"
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        node_names = session.exec(
            select(NodeExecutionModel.node_name)
            .where(NodeExecutionModel.run_id == "api-run-v1-retry")
            .order_by(NodeExecutionModel.sequence)
        ).all()
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1-retry")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        assert node_names.count("classify_question") == 1
        assert node_names[-4:] == ["generate_question_answer", "recommend_questions", "compose_final_reply", "finish"]
        assert "run.retry_requested" in [event.event_type for event in events]
        assert events[-1].event_type == "run.succeeded"
        _cleanup(session)


def test_runnable_graph_flow_demo_prints_node_status_and_outputs():
    demo_path = Path(__file__).with_name("run_graph_flow_demo.py")
    spec = importlib.util.spec_from_file_location("run_graph_flow_demo", demo_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with Session(engine) as session:
        _cleanup(session)

    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = module.main(["最近 7 天销售额", "--run-id", "api-demo-run-1"])

    text = output.getvalue()
    assert exit_code == 0
    assert "输入问题: 最近 7 天销售额" in text
    assert "创建并执行图响应" in text
    assert "run.succeeded" in text
    assert "节点: understand_question | 状态: succeeded" in text
    assert "节点: execute_sql | 状态: succeeded" in text
    assert "输出:" in text
    assert "最终回答: 这是图工作流占位回答：最近 7 天销售额" in text

    with Session(engine) as session:
        _cleanup(session)


def test_runnable_graph_flow_demo_can_print_chatbi_v1_nodes():
    demo_path = Path(__file__).with_name("run_graph_flow_demo.py")
    spec = importlib.util.spec_from_file_location("run_graph_flow_demo", demo_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_semantic_dataset(session)

    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = module.main(
            [
                "今日访问人数",
                "--definition-version",
                "v1",
                "--dataset-id",
                str(dataset_id),
                "--run-id",
                "api-demo-v1-run-1",
            ]
        )

    text = output.getvalue()
    assert exit_code == 0
    assert "definition_version: v1" in text
    assert "节点: classify_question | 状态: succeeded" in text
    assert '"metrics": [\n    "visit_uv"\n  ]' in text
    assert "节点: compose_final_reply | 状态: succeeded" in text
    assert "最终回答: 暂时无法生成完整回答，请稍后重试。" in text

    with Session(engine) as session:
        _cleanup(session)


def test_graph_run_query_and_events_are_scoped_to_current_user():
    with Session(engine) as session:
        _cleanup(session)
        _client().post(
            "/graph/queries",
            json={
                "question": "销售额",
                "dataset_id": 7001,
                "request_id": "api-request-2",
                "run_id": "api-run-2",
            },
        )

        run_response = _client().get("/graph/runs/api-run-2")
        event_response = _client().get("/graph/runs/api-run-2/events", params={"after_sequence": 0})
        forbidden = _client(_user(user_id=999, oid=9501)).get("/graph/runs/api-run-2")

        assert run_response.status_code == 200
        summary = run_response.json()["context_summary"]
        assert summary["question"] == "销售额"
        assert summary["dataset_id"] == 7001
        assert summary["variables"]["answer"]["answer"] == "这是图工作流占位回答：销售额"
        assert event_response.status_code == 200
        assert [event["sequence"] for event in event_response.json()["events"]] == list(
            range(1, len(event_response.json()["events"]) + 1)
        )
        assert event_response.json()["events"][-1]["event_type"] == "run.succeeded"
        assert forbidden.status_code == 404
        _cleanup(session)


def test_graph_interaction_response_cancel_and_retry_validate_ownership_and_state():
    with Session(engine) as session:
        _cleanup(session)
        _client().post(
            "/graph/queries",
            json={
                "question": "需要澄清的问题",
                "dataset_id": 7001,
                "request_id": "api-request-3",
                "run_id": "api-run-3",
            },
        )
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-run-3")).one()
        run.status = "waiting_input"
        session.add(
            InteractionRequestModel(
                interaction_id="api-interaction-1",
                run_id="api-run-3",
                node_name="clarify",
                status="pending",
                prompt="请选择指标",
                response_schema={},
                allowed_update_paths=["variables.metric"],
                options=[],
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    answer = _client().post(
        "/graph/runs/api-run-3/interactions/api-interaction-1/responses",
        json={"response": {"metric": "sales"}},
    )
    cancel = _client().post("/graph/runs/api-run-3/cancel")

    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == "api-interaction-1")
        ).one()
        interaction_response = interaction.response
        run = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-run-3")).one()
        run.status = "failed"
        run.error_code = "TEST_FAILURE"
        session.add(run)
        session.commit()

    retry = _client().post("/graph/runs/api-run-3/retry")
    forbidden = _client(_user(user_id=999, oid=9501)).post("/graph/runs/api-run-3/cancel")

    assert answer.status_code == 200
    assert answer.json()["status"] == "answered"
    assert interaction_response == {"metric": "sales"}
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert retry.status_code == 200
    assert retry.json()["status"] == "created"
    assert forbidden.status_code == 404

    with Session(engine) as session:
        _cleanup(session)
