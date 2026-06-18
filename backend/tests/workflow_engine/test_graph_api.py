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


def test_graph_query_creates_independent_run_and_public_created_event():
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
    assert body["status"] == "created"

    with Session(engine) as session:
        stored = session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-run-1")).one()
        events = session.exec(select(WorkflowEventModel).where(WorkflowEventModel.run_id == "api-run-1")).all()
        assert stored.oid == 9501
        assert stored.user_id == 501
        assert stored.request == {
            "tenant_id": 9501,
            "user_id": 501,
            "question": "最近 7 天销售额",
            "datasource_id": 7001,
            "request_id": "api-request-1",
        }
        assert [event.event_type for event in events] == ["run.created"]
        assert events[0].public_payload == {"status": "created", "question": "最近 7 天销售额"}
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
        assert run_response.json()["context_summary"] == {
            "question": "销售额",
            "datasource_id": 7001,
            "variables": {},
        }
        assert event_response.status_code == 200
        assert [event["sequence"] for event in event_response.json()["events"]] == [1]
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
