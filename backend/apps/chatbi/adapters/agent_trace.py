"""ChatBI Agent Trace 的技术适配器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from apps.chatbi.models import ResultArtifactWriteData
from apps.chatbi.repository.sqlmodel import agent_trace_repository
from apps.chatbi.services.execution import ResultArtifactError, ResultArtifactService
from apps.conversation import ChatRecordExecutionType
from apps.trace import (
    TraceContractError,
    TraceDetailWriteInput,
    TraceError,
    TraceNodeFinishInput,
    TraceNodeRef,
    TraceNodeStartInput,
    TraceWriteError,
)


class ChatBITraceRepository:
    """在独立短 Session 中持久化 ChatBI Agent Trace。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def ensure_run_root(
        self,
        data: TraceNodeStartInput,
    ) -> tuple[TraceNodeRef, bool]:
        with self._session_factory() as session:
            try:
                node, created = agent_trace_repository.ensure_run_root(session, data)
                result = agent_trace_repository.to_ref(node)
                session.commit()
                return result, created
            except TraceError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise TraceWriteError("TRACE_ROOT_WRITE_FAILED") from exc

    def start_node(self, data: TraceNodeStartInput) -> TraceNodeRef:
        with self._session_factory() as session:
            try:
                node = agent_trace_repository.start_node(session, data)
                result = agent_trace_repository.to_ref(node)
                session.commit()
                return result
            except TraceError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise TraceWriteError("TRACE_NODE_START_FAILED") from exc

    def finish_node(self, data: TraceNodeFinishInput) -> None:
        with self._session_factory() as session:
            try:
                agent_trace_repository.finish_node(session, data)
                session.commit()
            except TraceError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise TraceWriteError("TRACE_NODE_FINISH_FAILED") from exc

    def mark_run_partial(self, run_id: int, *, lost_nodes: int = 1) -> None:
        with self._session_factory() as session:
            try:
                agent_trace_repository.mark_run_partial(
                    session,
                    run_id,
                    lost_nodes=lost_nodes,
                )
                session.commit()
            except TraceError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise TraceWriteError("TRACE_PARTIAL_MARK_FAILED") from exc


class ChatBITraceDetailGateway:
    """通过现有 Artifact Gateway 写入单节点脱敏详情。"""

    _KINDS = {
        "input": "agent_trace_input",
        "output": "agent_trace_output",
    }

    def __init__(
        self,
        session_factory: Callable[[], Session],
        service_factory: Callable[[Session], ResultArtifactService],
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory

    def write(self, data: TraceDetailWriteInput) -> dict[str, Any]:
        try:
            kind = self._KINDS[data.side]
        except KeyError as exc:
            raise TraceContractError("TRACE_DETAIL_SIDE_INVALID") from exc

        with self._session_factory() as session:
            try:
                artifact = self._service_factory(session).save(
                    ResultArtifactWriteData(
                        execution_id=f"agent:{data.run_id}",
                        execution_type=ChatRecordExecutionType.AGENT,
                        chat_id=data.chat_id,
                        record_id=data.record_id,
                        kind=kind,
                        payload=data.payload,
                        metadata={
                            **data.metadata,
                            "run_id": data.run_id,
                            "node_id": data.node_id,
                            "side": data.side,
                        },
                    )
                )
            except ResultArtifactError as exc:
                raise TraceWriteError("TRACE_DETAIL_WRITE_FAILED") from exc
            return artifact.model_dump(mode="json")


__all__ = ["ChatBITraceDetailGateway", "ChatBITraceRepository"]
