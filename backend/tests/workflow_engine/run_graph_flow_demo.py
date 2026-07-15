from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

MINIMAL_NODE_OUTPUT_PATHS = {
    "understand_question": ("variables", "understanding"),
    "retrieve_schema": ("variables", "schema"),
    "generate_sql": ("variables", "sql"),
    "validate_sql": ("variables", "sql_validation"),
    "apply_permission": ("variables", "permission"),
    "execute_sql": ("variables", "sql_result"),
    "generate_answer": ("variables", "answer"),
    "finish": ("variables", "completed"),
}

V1_NODE_OUTPUT_PATHS = {
    "classify_question": ("variables", "classification"),
    "reject_answer": ("variables", "answer"),
    "chitchat_answer": ("variables", "answer"),
    "rewrite_question": ("variables", "rewrite"),
    "ask_rewrite_clarification": ("control", "pending_interaction_id"),
    "draw_image_profile": ("variables", "image_profile"),
    "recognize_intent": ("variables", "intent"),
    "ask_intent_clarification": ("control", "pending_interaction_id"),
    "retrieve_knowledge": ("variables", "knowledge"),
    "ask_metric_selection": ("control", "pending_interaction_id"),
    "bind_query_plan": ("variables", "plan"),
    "generate_sql": ("variables", "sql"),
    "execute_sql": ("variables", "execution"),
    "handle_sql_error": ("variables", "sql_error"),
    "generate_question_answer": ("variables", "answer"),
    "recommend_questions": ("variables", "recommendations"),
    "compose_final_reply": ("variables", "final_reply"),
    "finish": ("variables", "completed"),
}


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _client(user_id: int, oid: int):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.workflow_engine.api import router as graph_router
    from common.core.deps import get_current_user

    app = FastAPI()
    app.include_router(graph_router.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, oid=oid)
    return TestClient(app)


def _read_path(data: dict, path: tuple[str, ...]) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _load_run(run_id: str):
    from sqlmodel import Session, select

    from apps.workflow_engine.infrastructure.persistence.models import WorkflowRunModel
    from common.core.db import engine

    with Session(engine) as session:
        return session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one()


def _load_events(run_id: str) -> list:
    from sqlmodel import Session, select

    from apps.workflow_engine.infrastructure.persistence.models import (
        WorkflowEventModel,
    )
    from common.core.db import engine

    with Session(engine) as session:
        return session.exec(
            select(WorkflowEventModel)
            .where(WorkflowEventModel.run_id == run_id)
            .order_by(WorkflowEventModel.sequence)
        ).all()


def _mark_events_published(run_id: str) -> None:
    from sqlmodel import Session, select

    from apps.workflow_engine.infrastructure.persistence.models import (
        WorkflowEventModel,
    )
    from common.core.db import engine

    with Session(engine) as session:
        events = session.exec(select(WorkflowEventModel).where(WorkflowEventModel.run_id == run_id)).all()
        for event in events:
            # 演示脚本已经直接打印事件，标记为已发布以免污染后续 outbox 测试。
            event.publish_status = "published"
            session.add(event)
        session.commit()


def _node_status(events: list, node_name: str) -> str:
    event_types = [event.event_type for event in events if event.node_name == node_name]
    if "node.failed" in event_types:
        return "failed"
    if "node.succeeded" in event_types:
        return "succeeded"
    if "node.started" in event_types:
        return "started"
    return "not_run"


def _node_output_paths(definition_version: str) -> dict[str, tuple[str, ...]]:
    if definition_version == "v1":
        return V1_NODE_OUTPUT_PATHS
    return MINIMAL_NODE_OUTPUT_PATHS


def run_demo(question: str, dataset_id: int, run_id: str, user_id: int, oid: int, definition_version: str) -> int:
    node_output_paths = _node_output_paths(definition_version)
    print("=== Graph Workflow Demo ===")
    print(f"输入问题: {question}")
    print(f"dataset_id: {dataset_id}")
    print(f"definition_version: {definition_version}")
    print(f"run_id: {run_id}")
    print(f"要执行节点顺序: {', '.join(node_output_paths.keys())}")

    response = _client(user_id=user_id, oid=oid).post(
        "/graph/queries",
        json={
            "question": question,
            "dataset_id": dataset_id,
            "definition_version": definition_version,
            "request_id": f"demo-request-{uuid4().hex[:8]}",
            "run_id": run_id,
        },
    )

    print("\n=== 创建并执行图响应 ===")
    print(f"HTTP 状态: {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        print(response.text)
        return 1
    print(_json(body))
    if response.status_code >= 400:
        return 1

    run = _load_run(run_id)
    events = _load_events(run_id)
    context = run.context or {}
    variables = context.get("variables", {})

    print("\n=== 图执行事件日志 ===")
    for event in events:
        node = event.node_name or "-"
        payload = event.public_payload or {}
        print(
            f"#{event.sequence:02d} | 事件: {event.event_type} | 节点: {node} | "
            f"公开载荷: {_json(payload)}"
        )

    print("\n=== 节点执行状态与输出 ===")
    for node_name, output_path in node_output_paths.items():
        status = _node_status(events, node_name)
        # 未执行节点不展示共享变量路径上的后续结果，避免误导人工排查。
        output = None if status == "not_run" else _read_path(context, output_path)
        print(f"节点: {node_name} | 状态: {status}")
        print(f"输出: {_json(output)}")

    answer = variables.get("final_reply", {}).get("final_answer") or variables.get("answer", {}).get("answer")
    print("\n=== 最终结果 ===")
    print(f"最终状态: {run.status}")
    print(f"最终节点: {run.current_node}")
    print(f"最终回答: {answer}")
    _mark_events_published(run_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行一次 Graph Workflow 占位闭环并打印节点日志。")
    parser.add_argument("question", nargs="?", default="最近 7 天销售额")
    parser.add_argument("--dataset-id", type=int, default=7001)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--user-id", type=int, default=501)
    parser.add_argument("--oid", type=int, default=9501)
    parser.add_argument("--definition-version", choices=["minimal-v1", "v1"], default="minimal-v1")
    args = parser.parse_args(argv)

    run_id = args.run_id or f"demo-run-{uuid4().hex[:12]}"
    return run_demo(
        question=args.question,
        dataset_id=args.dataset_id,
        run_id=run_id,
        user_id=args.user_id,
        oid=args.oid,
        definition_version=args.definition_version,
    )


if __name__ == "__main__":
    raise SystemExit(main())
