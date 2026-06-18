import importlib.util
import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from apps.workflow_engine.api import router as graph_router
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from common.core.db import engine
from common.core.deps import get_current_user


def _user(user_id: int = 501, oid: int = 9501):
    return SimpleNamespace(id=user_id, oid=oid)


def _client(user=None) -> TestClient:
    app = FastAPI()
    app.include_router(graph_router.router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def _cleanup(session: Session) -> None:
    session.execute(delete(InteractionRequestModel).where(InteractionRequestModel.run_id.like("api-%")))
    session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.like("api-%")))
    session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.like("api-%")))
    session.commit()


def test_graph_routes_are_registered_and_included_by_apps_api():
    app = FastAPI()
    app.include_router(graph_router.router)

    paths = app.openapi()["paths"]

    expected_routes = {
        "/graph/queries",
        "/graph/runs/{run_id}",
        "/graph/runs/{run_id}/events",
        "/graph/runs/{run_id}/trace",
        "/graph/runs/{run_id}/interactions/{interaction_id}/responses",
        "/graph/runs/{run_id}/cancel",
        "/graph/runs/{run_id}/retry",
    }
    assert expected_routes.issubset(paths.keys())

    api_py = Path(__file__).parents[2] / "apps" / "api.py"
    source = api_py.read_text()
    assert "from apps.workflow_engine.api import router as graph_workflow" in source
    assert "include_router(graph_workflow.router)" in source


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
            "datasource_id": 7001,
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
        assert stored.request == {
            "tenant_id": 9501,
            "user_id": 501,
            "question": "最近 7 天销售额",
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


def test_graph_query_can_execute_placeholder_chatbi_v1_graph():
    with Session(engine) as session:
        _cleanup(session)

    response = _client().post(
        "/graph/queries",
        json={
            "question": "最近 7 天销售额",
            "datasource_id": 7001,
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
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "这是图工作流占位回答：最近 7 天销售额"

    with Session(engine) as session:
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        assert "classify_question" in [event.node_name for event in events]
        _cleanup(session)


def test_graph_v1_interaction_response_resumes_runtime_to_final_reply():
    with Session(engine) as session:
        _cleanup(session)

    created = _client().post(
        "/graph/queries",
        json={
            "question": "需要澄清的问题",
            "datasource_id": 7001,
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
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "这是图工作流占位回答：需要澄清的问题"

    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == interaction_id)
        ).one()
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1-clarify")
            .order_by(WorkflowEventModel.sequence)
        ).all()
        assert interaction.status == "answered"
        assert events[-1].event_type == "run.succeeded"
        _cleanup(session)


def test_graph_trace_returns_node_status_route_reason_and_outputs():
    with Session(engine) as session:
        _cleanup(session)

    _client().post(
        "/graph/queries",
        json={
            "question": "最近 7 天销售额",
            "datasource_id": 7001,
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
    assert nodes["compose_final_reply"]["output"]["final_answer"] == "这是图工作流占位回答：最近 7 天销售额"

    with Session(engine) as session:
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

    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = module.main(
            [
                "最近 7 天销售额",
                "--definition-version",
                "v1",
                "--run-id",
                "api-demo-v1-run-1",
            ]
        )

    text = output.getvalue()
    assert exit_code == 0
    assert "definition_version: v1" in text
    assert "节点: classify_question | 状态: succeeded" in text
    assert "节点: compose_final_reply | 状态: succeeded" in text
    assert "最终回答: 这是图工作流占位回答：最近 7 天销售额" in text

    with Session(engine) as session:
        _cleanup(session)


def test_graph_run_query_and_events_are_scoped_to_current_user():
    with Session(engine) as session:
        _cleanup(session)
        _client().post(
            "/graph/queries",
            json={
                "question": "销售额",
                "datasource_id": 7001,
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
        assert summary["datasource_id"] == 7001
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
                "datasource_id": 7001,
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
