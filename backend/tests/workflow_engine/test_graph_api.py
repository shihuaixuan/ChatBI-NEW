import importlib.util
import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from apps.chatbi_workflow import runtime as chatbi_runtime
from apps.headless.models import (
    HeadlessAssetDocument,
    HeadlessDataSet,
    HeadlessDataSetAsset,
    HeadlessDataSetModelConfig,
    HeadlessDimension,
    HeadlessDomain,
    HeadlessMetric,
    HeadlessModel,
    HeadlessSchemaIndex,
)
from apps.workflow_engine.api import router as graph_router
from apps.workflow_engine.api import service as graph_service
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from common.core.db import engine
from common.core.deps import get_current_user


@pytest.fixture(autouse=True)
def _fake_chatbi_v1_sql_execute_tool(monkeypatch):
    class FakeSqlExecuteTool:
        def __init__(self, session) -> None:
            self.session = session

        def run(self, payload: dict):
            return SimpleNamespace(
                success=True,
                payload={"fields": [], "data": [{"placeholder_value": 1}], "execution_ms": 1},
                error_code=None,
                message=None,
            )

    monkeypatch.setattr(chatbi_runtime, "SqlExecuteTool", FakeSqlExecuteTool)


@pytest.fixture(autouse=True)
def _fake_chatbi_v1_question_model(monkeypatch):
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
                return '{"intent_type":"metric_query","confidence":0.9,"ambiguous_slots":[],"conflict_slots":[]}'
            return '{"category":"data","reason":"测试模型分类为数据问题","risk_level":"low","confidence":0.9}'

    def build_runtime(session):
        return chatbi_runtime.build_real_chatbi_v1_runtime(
            session,
            question_model_client=FakeQuestionModelClient(),
        )

    monkeypatch.setattr(graph_service, "build_real_chatbi_v1_runtime", build_runtime)


def _user(user_id: int = 501, oid: int = 9501):
    return SimpleNamespace(id=user_id, oid=oid)


def _client(user=None) -> TestClient:
    app = FastAPI()
    app.include_router(graph_router.router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def _cleanup(session: Session) -> None:
    session.execute(delete(InteractionRequestModel).where(InteractionRequestModel.run_id.like("api-%")))
    session.execute(delete(NodeExecutionModel).where(NodeExecutionModel.run_id.like("api-%")))
    session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.like("api-%")))
    session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.like("api-%")))
    _cleanup_headless_fixture(session)
    session.commit()


def _cleanup_headless_fixture(session: Session, oid: int = 9501) -> None:
    dataset_ids = session.exec(
        select(HeadlessDataSet.id).where(HeadlessDataSet.oid == oid, HeadlessDataSet.biz_name == "api_stall_dataset")
    ).all()
    model_ids = session.exec(
        select(HeadlessModel.id).where(HeadlessModel.oid == oid, HeadlessModel.biz_name == "api_stall_traffic_model")
    ).all()
    domain_ids = session.exec(
        select(HeadlessDomain.id).where(HeadlessDomain.oid == oid, HeadlessDomain.biz_name == "api_graph_v1_domain")
    ).all()
    if dataset_ids:
        session.execute(delete(HeadlessAssetDocument).where(HeadlessAssetDocument.dataset_id.in_(dataset_ids)))
        session.execute(delete(HeadlessSchemaIndex).where(HeadlessSchemaIndex.dataset_id.in_(dataset_ids)))
        session.execute(delete(HeadlessDataSetAsset).where(HeadlessDataSetAsset.dataset_id.in_(dataset_ids)))
        session.execute(delete(HeadlessDataSetModelConfig).where(HeadlessDataSetModelConfig.dataset_id.in_(dataset_ids)))
        session.execute(delete(HeadlessDataSet).where(HeadlessDataSet.id.in_(dataset_ids)))
    if model_ids:
        session.execute(delete(HeadlessMetric).where(HeadlessMetric.model_id.in_(model_ids)))
        session.execute(delete(HeadlessDimension).where(HeadlessDimension.model_id.in_(model_ids)))
        session.execute(delete(HeadlessModel).where(HeadlessModel.id.in_(model_ids)))
    if domain_ids:
        session.execute(delete(HeadlessDomain).where(HeadlessDomain.id.in_(domain_ids)))


def _seed_v1_headless_dataset(session: Session, oid: int = 9501) -> int:
    _cleanup_headless_fixture(session, oid=oid)
    domain = HeadlessDomain(oid=oid, name="API 测试域", biz_name="api_graph_v1_domain")
    session.add(domain)
    session.flush()

    model = HeadlessModel(
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
            "fields": [{"fieldName": "visit_uv", "dataType": "BIGINT"}],
            "measures": [{"name": "访问人数", "bizName": "visit_uv", "expr": "visit_uv", "agg": "SUM"}],
        },
    )
    session.add(model)
    session.flush()

    metric = HeadlessMetric(
        oid=oid,
        model_id=model.id or 0,
        name="访问人数",
        biz_name="visit_uv",
        alias=["访客数"],
        description="店铺访问人数 UV",
        default_agg="SUM",
        fields=["visit_uv"],
    )
    dimension = HeadlessDimension(
        oid=oid,
        model_id=model.id or 0,
        name="档口",
        biz_name="stall_id",
        description="店铺档口维度",
    )
    session.add(metric)
    session.add(dimension)
    session.flush()

    dataset = HeadlessDataSet(
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
                    "dimensions": [dimension.id],
                }
            ]
        },
    )
    session.add(dataset)
    session.flush()
    session.commit()
    return dataset.id or 0


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
        assert stored.request == {
            "tenant_id": 9501,
            "user_id": 501,
            "question": "最近 7 天销售额",
            "dataset_id": 7001,
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


def test_graph_query_can_execute_chatbi_v1_graph():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_headless_dataset(session)

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
        _cleanup(session)


def test_graph_v1_classification_model_failure_stops_run(monkeypatch):
    class FailingQuestionModelClient:
        def __call__(self, prompt):
            raise RuntimeError("model unavailable")

    def build_runtime(session):
        return chatbi_runtime.build_real_chatbi_v1_runtime(
            session,
            question_model_client=FailingQuestionModelClient(),
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
    assert body["status"] == "failed"
    assert body["current_node"] == "classify_question"

    with Session(engine) as session:
        executions = session.exec(
            select(NodeExecutionModel)
            .where(NodeExecutionModel.run_id == "api-run-v1-classify-failed")
            .order_by(NodeExecutionModel.sequence)
        ).all()
        assert [(execution.node_name, execution.status, execution.error_code) for execution in executions] == [
            ("classify_question", "failed", "QUESTION_CLASSIFY_FAILED")
        ]
        _cleanup(session)


def test_graph_v1_interaction_response_resumes_runtime_to_final_reply():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_headless_dataset(session)

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
        dataset_id = _seed_v1_headless_dataset(session)

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
    assert nodes["generate_sql"]["output"] == {
        "statement_type": "select",
        "sql_redacted": True,
        "artifact_ref": None,
    }
    assert nodes["execute_sql"]["output"] == {
        "status": "succeeded",
        "row_count": 1,
        "fields": [],
        "execution_ms": 1,
        "artifact_ref": None,
    }
    assert nodes["compose_final_reply"]["output"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        _cleanup(session)


def test_graph_query_persists_node_execution_summaries_for_trace_and_retry():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_headless_dataset(session)

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
            "generate_sql",
            "execute_sql",
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


def test_graph_v1_retry_restarts_failed_run_and_executes_graph():
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_headless_dataset(session)

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
        run.current_node = "classify_question"
        context = run.context
        context["control"]["current_node"] = "classify_question"
        context["control"]["previous_node"] = None
        context["control"]["executed_nodes"] = 0
        context["control"]["loop_iterations"] = {}
        context["variables"] = {}
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
    assert body["context_summary"]["variables"]["final_reply"]["final_answer"] == "暂时无法生成完整回答，请稍后重试。"

    with Session(engine) as session:
        events = session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == "api-run-v1-retry")
            .order_by(WorkflowEventModel.sequence)
        ).all()
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
        dataset_id = _seed_v1_headless_dataset(session)

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
