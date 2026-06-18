from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from apps.chatbi_workflow.runtime import build_placeholder_chatbi_runtime
from apps.workflow_engine.api.schemas import (
    ControlResponse,
    GraphEventListResponse,
    GraphEventResponse,
    GraphQueryRequest,
    GraphRunResponse,
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
        runtime = build_placeholder_chatbi_runtime(self._session)
        created = runtime.create_run(
            run_id=run_id,
            definition_name="chatbi",
            definition_version="minimal-v1",
            context=WorkflowContext(request=request_context, conversation={"question": request.question}),
        )
        runtime.execute(created.run_id)
        self._session.commit()
        return self._to_run_response(self._load_owned_run(current_user, created.run_id))

    def get_run(self, current_user: Any, run_id: str) -> GraphRunResponse:
        return self._to_run_response(self._load_owned_run(current_user, run_id))

    def list_events(self, current_user: Any, run_id: str, after_sequence: int = 0) -> GraphEventListResponse:
        self._load_owned_run(current_user, run_id)
        events = EventStream(self._session).list(run_id=run_id, after_sequence=after_sequence)
        return GraphEventListResponse(events=[self._to_event_response(event) for event in events])

    def answer_interaction(
        self,
        current_user: Any,
        run_id: str,
        interaction_id: str,
        request: InteractionResponseRequest,
    ) -> ControlResponse:
        self._load_owned_run(current_user, run_id)
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

    def _to_event_response(self, event: WorkflowEvent) -> GraphEventResponse:
        return GraphEventResponse(
            event_id=event.event_id,
            sequence=event.sequence,
            event_type=event.event_type,
            node_name=event.node_name,
            public_payload=event.public_payload,
            created_at=event.created_at.isoformat(),
        )
