from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from apps.chatbi_workflow.runtime import (
    build_placeholder_chatbi_runtime,
    build_placeholder_chatbi_v1_runtime,
)
from apps.workflow_engine.api.schemas import (
    ControlResponse,
    GraphEventListResponse,
    GraphEventResponse,
    GraphQueryRequest,
    GraphRunResponse,
    GraphTraceNodeResponse,
    GraphTraceResponse,
    InteractionResponseRequest,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.domain.run import RunStatus
from apps.workflow_engine.infrastructure.events.outbox import EventOutbox
from apps.workflow_engine.infrastructure.events.stream import EventStream
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    WorkflowEventModel,
    WorkflowRunModel,
)

MINIMAL_TRACE_OUTPUT_PATHS = {
    "understand_question": ("variables", "understanding"),
    "retrieve_schema": ("variables", "schema"),
    "generate_sql": ("variables", "sql"),
    "validate_sql": ("variables", "sql_validation"),
    "apply_permission": ("variables", "permission"),
    "execute_sql": ("variables", "sql_result"),
    "generate_answer": ("variables", "answer"),
    "finish": ("variables", "completed"),
}

V1_TRACE_OUTPUT_PATHS = {
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
    "generate_sql": ("variables", "sql"),
    "execute_sql": ("variables", "sql_execution"),
    "handle_sql_error": ("variables", "sql_error"),
    "generate_question_answer": ("variables", "answer"),
    "recommend_questions": ("variables", "recommendations"),
    "compose_final_reply": ("variables", "final_reply"),
    "finish": ("variables", "completed"),
}


class GraphApiService:
    """Graph API 应用服务。

    当前阶段只负责独立 Run 的创建、查询、事件续传和控制语义。业务图执行会在
    `chatbi_workflow` 接入后由 Runtime 推进，API 层不依赖旧 Agentic 编排。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_query(self, current_user: Any, request: GraphQueryRequest) -> GraphRunResponse:
        run_id = request.run_id or f"graph-{uuid4().hex}"
        request_context = {
            "tenant_id": current_user.oid,
            "user_id": current_user.id,
            "question": request.question,
            "datasource_id": request.datasource_id,
            "request_id": request.request_id,
        }
        runtime = self._build_runtime(request.definition_version)
        created = runtime.create_run(
            run_id=run_id,
            definition_name="chatbi",
            definition_version=request.definition_version,
            context=WorkflowContext(request=request_context, conversation={"question": request.question}),
        )
        runtime.execute(created.run_id)
        self._session.commit()
        return self._to_run_response(self._load_owned_run(current_user, created.run_id))

    def _build_runtime(self, definition_version: str):
        """按请求版本选择当前可用的 ChatBI 占位图运行时。"""

        if definition_version == "minimal-v1":
            return build_placeholder_chatbi_runtime(self._session)
        if definition_version == "v1":
            return build_placeholder_chatbi_v1_runtime(self._session)
        raise HTTPException(status_code=400, detail="UNSUPPORTED_GRAPH_DEFINITION_VERSION")

    def get_run(self, current_user: Any, run_id: str) -> GraphRunResponse:
        return self._to_run_response(self._load_owned_run(current_user, run_id))

    def list_events(self, current_user: Any, run_id: str, after_sequence: int = 0) -> GraphEventListResponse:
        self._load_owned_run(current_user, run_id)
        events = EventStream(self._session).list(run_id=run_id, after_sequence=after_sequence)
        return GraphEventListResponse(events=[self._to_event_response(event) for event in events])

    def get_trace(self, current_user: Any, run_id: str) -> GraphTraceResponse:
        run = self._load_owned_run(current_user, run_id)
        events = EventStream(self._session).list(run_id=run_id, after_sequence=0)
        context = run.context or {}
        output_paths = self._trace_output_paths(run.definition_version)
        return GraphTraceResponse(
            run_id=run.run_id,
            status=run.status,
            current_node=run.current_node,
            nodes=[
                GraphTraceNodeResponse(
                    name=node_name,
                    status=self._trace_node_status(events, node_name),
                    route_reason=self._trace_route_reason(events, node_name),
                    output=self._trace_node_output(context, node_name, output_path, events),
                )
                for node_name, output_path in output_paths.items()
            ],
        )

    def answer_interaction(
        self,
        current_user: Any,
        run_id: str,
        interaction_id: str,
        request: InteractionResponseRequest,
    ) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        if run.definition_version == "v1" and run.status == RunStatus.WAITING_INPUT.value:
            runtime = self._build_runtime(run.definition_version)
            resumed = runtime.resume(
                run_id=run_id,
                interaction_id=interaction_id,
                response=request.response,
                tenant_id=current_user.oid,
                user_id=current_user.id,
            )
            self._session.commit()
            return ControlResponse(run_id=run_id, status=resumed.status.value)

        interaction = self._session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == run_id,
                InteractionRequestModel.interaction_id == interaction_id,
            )
        ).one_or_none()
        if interaction is None or interaction.status != "pending":
            raise HTTPException(status_code=409, detail="INTERACTION_NOT_PENDING")
        interaction.status = "answered"
        interaction.response = request.response
        interaction.answered_at = datetime.now(timezone.utc)
        self._session.add(interaction)
        self._append_control_event(run_id, "interaction.answered", {"status": "answered"})
        self._session.commit()
        return ControlResponse(run_id=run_id, status="answered")

    def cancel(self, current_user: Any, run_id: str) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        run.status = RunStatus.CANCELLED.value
        run.version += 1
        run.updated_at = datetime.now(timezone.utc)
        self._session.add(run)
        self._append_control_event(run_id, "run.cancelled", {"status": "cancelled"})
        self._session.commit()
        return ControlResponse(run_id=run_id, status=RunStatus.CANCELLED.value)

    def retry(self, current_user: Any, run_id: str) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        if run.status != RunStatus.FAILED.value:
            raise HTTPException(status_code=409, detail="RUN_NOT_FAILED")
        run.status = RunStatus.CREATED.value
        run.current_node = "start"
        run.error_code = None
        run.version += 1
        run.updated_at = datetime.now(timezone.utc)
        self._session.add(run)
        self._append_control_event(run_id, "run.retry_requested", {"status": "created"})
        self._session.commit()
        return ControlResponse(run_id=run_id, status=RunStatus.CREATED.value)

    def _load_owned_run(self, current_user: Any, run_id: str) -> WorkflowRunModel:
        run = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one_or_none()
        if run is None or run.oid != current_user.oid or run.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="GRAPH_RUN_NOT_FOUND")
        return run

    def _append_control_event(self, run_id: str, event_type: str, public_payload: dict[str, Any]) -> None:
        EventOutbox(self._session).append(
            WorkflowEvent(
                event_id=str(uuid4()),
                run_id=run_id,
                sequence=self._next_sequence(run_id),
                event_type=event_type,
                public_payload=public_payload,
                created_at=datetime.now(timezone.utc),
            )
        )

    def _next_sequence(self, run_id: str) -> int:
        latest_sequence = self._session.exec(
            select(func.max(col(WorkflowEventModel.sequence))).where(WorkflowEventModel.run_id == run_id)
        ).one()
        return int(latest_sequence or 0) + 1

    def _to_run_response(self, run: WorkflowRunModel) -> GraphRunResponse:
        context = run.context or {}
        request = run.request or context.get("request", {})
        variables = context.get("variables", {})
        return GraphRunResponse(
            run_id=run.run_id,
            status=run.status,
            current_node=run.current_node,
            output=run.output or {},
            context_summary={
                "question": request.get("question"),
                "datasource_id": request.get("datasource_id"),
                "variables": variables,
            },
        )

    def _trace_output_paths(self, definition_version: str) -> dict[str, tuple[str, ...]]:
        if definition_version == "v1":
            return V1_TRACE_OUTPUT_PATHS
        return MINIMAL_TRACE_OUTPUT_PATHS

    def _trace_node_status(self, events: list[WorkflowEvent], node_name: str) -> str:
        event_types = [event.event_type for event in events if event.node_name == node_name]
        if "node.failed" in event_types:
            return "failed"
        if "node.succeeded" in event_types:
            return "succeeded"
        if "node.started" in event_types:
            return "started"
        return "not_run"

    def _trace_route_reason(self, events: list[WorkflowEvent], node_name: str) -> str | None:
        for event in reversed(events):
            if event.node_name == node_name and event.event_type == "node.routed":
                reason_code = event.public_payload.get("reason_code") if event.public_payload else None
                return str(reason_code) if reason_code is not None else None
        return None

    def _trace_node_output(
        self,
        context: dict[str, Any],
        node_name: str,
        output_path: tuple[str, ...],
        events: list[WorkflowEvent],
    ) -> Any:
        # 未执行节点不读取共享变量路径，避免把其他节点后写入的结果误归因到该节点。
        if self._trace_node_status(events, node_name) == "not_run":
            return None
        return self._read_context_path(context, output_path)

    def _read_context_path(self, context: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = context
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        return current

    def _to_event_response(self, event: WorkflowEvent) -> GraphEventResponse:
        return GraphEventResponse(
            event_id=event.event_id,
            sequence=event.sequence,
            event_type=event.event_type,
            node_name=event.node_name,
            public_payload=event.public_payload,
            created_at=event.created_at.isoformat(),
        )
