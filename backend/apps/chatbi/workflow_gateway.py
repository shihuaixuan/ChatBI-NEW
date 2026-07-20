from typing import Any

import orjson
from sqlmodel import Session

from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    Chat,
    ChatRecord,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ExecutionBindingData,
)
from apps.chatbi.services import (
    ExecutionBindingError,
    resolve_execution_binding,
)


class WorkflowChatRecordGateway:
    """供业务 Graph API 使用的 ChatRecord 投影网关。"""

    def __init__(self, session: Session) -> None:
        self._service = build_chat_record_service(session)

    def project(
        self,
        *,
        record_id: int,
        chat_id: int,
        run_id: str,
        status: str,
        variables: dict[str, Any],
    ) -> ChatRecord:
        try:
            record = self._service.get(record_id)
        except ValueError as exc:
            raise ValueError("GRAPH_CHAT_RECORD_NOT_FOUND") from exc
        if record.chat_id != chat_id:
            raise ValueError("GRAPH_CHAT_RECORD_NOT_FOUND")

        result = None
        if status == "succeeded":
            final_reply = variables.get("final_reply") or {}
            legacy_answer = variables.get("answer") or {}
            answer = str(
                final_reply.get("final_answer") or legacy_answer.get("answer") or ""
            ).strip()
            if not answer:
                raise ValueError("GRAPH_RESULT_NOT_PROJECTABLE")
            sql_payload = variables.get("sql") or {}
            sql = sql_payload.get("sql") if isinstance(sql_payload, dict) else None
            chart = final_reply.get("chart")
            result = ChatRecordResultProjection(
                answer=answer,
                sql=sql,
                chart=(
                    orjson.dumps(chart).decode() if chart is not None else None
                ),
            )

        return self._service.transition(
            record,
            status,
            expected_chat_id=chat_id,
            trace_id=run_id,
            execution_type=ChatRecordExecutionType.GRAPH,
            error="GRAPH_RUN_FAILED" if status == "failed" else None,
            result=result,
        )


def build_workflow_chat_record_gateway(
    session: Session,
) -> WorkflowChatRecordGateway:
    """装配 Graph 到 ChatRecord 的公开投影网关。"""

    return WorkflowChatRecordGateway(session)


__all__ = [
    "Chat",
    "ChatRecord",
    "ChatRecordCreateData",
    "ChatRecordExecutionType",
    "ExecutionBindingData",
    "ExecutionBindingError",
    "resolve_execution_binding",
    "build_chat_record_service",
    "build_workflow_chat_record_gateway",
]
