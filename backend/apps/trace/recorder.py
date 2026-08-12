"""持久化调用树与 OpenTelemetry 导出的统一 Recorder。"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from apps.trace.attributes import sanitize_attributes
from apps.trace.errors import TraceContractError, TraceError
from apps.trace.models import (
    TRACE_TERMINAL_STATUSES,
    TraceDetailWriteInput,
    TraceNodeFinishInput,
    TraceNodeRef,
    TraceNodeSpec,
    TraceNodeStartInput,
    TraceNodeStatus,
    TraceNodeType,
)
from apps.trace.ports import (
    DisabledTraceExporter,
    TraceDetailGateway,
    TraceExportClient,
    TraceExportSpan,
    TraceRepository,
)
from apps.trace.redaction import redact_trace_mapping, redact_trace_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TraceFrame:
    node: TraceNodeRef
    chat_id: int | None
    record_id: int | None


_active_frame: ContextVar[_TraceFrame | None] = ContextVar(
    "agent_trace_active_frame",
    default=None,
)


class TraceNodeHandle:
    """业务操作在节点生命周期内写入结果和状态。"""

    def __init__(self, export_span: TraceExportSpan) -> None:
        self._export_span = export_span
        self.output_summary: dict[str, Any] = {}
        self.output_detail: dict[str, Any] | None = None
        self.state_diff: dict[str, Any] = {}
        self.token_usage: dict[str, Any] = {}
        self.status = TraceNodeStatus.SUCCEEDED
        self.error_code: str | None = None
        self.error_category: str | None = None
        self.error: str | None = None
        self.metadata: dict[str, Any] = {}

    def set_output(self, data: Mapping[str, Any]) -> None:
        self.output_summary = redact_trace_mapping(data)

    def set_output_detail(self, data: Mapping[str, Any]) -> None:
        self.output_detail = redact_trace_mapping(data)

    def set_state_diff(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> None:
        self.state_diff = redact_trace_mapping(
            {"before": dict(before), "after": dict(after)}
        )

    def set_token_usage(self, usage: Mapping[str, Any]) -> None:
        self.token_usage = redact_trace_mapping(usage)

    def set_attribute(self, name: str, value: Any) -> None:
        """属性只按低基数白名单进入外部 Span 和节点元数据。"""

        safe = sanitize_attributes({name: value})
        if not safe:
            return
        self.metadata.update(safe)
        self._export_span.set_attribute(name, value)

    def set_status(self, status: TraceNodeStatus) -> None:
        if status not in TRACE_TERMINAL_STATUSES:
            raise TraceContractError("TRACE_TERMINAL_STATUS_REQUIRED")
        self.status = status

    def set_error(
        self,
        code: str | None,
        category: str | None,
        message: str,
    ) -> None:
        self.error_code = code
        self.error_category = category
        self.error = str(redact_trace_payload(message))
        self.status = TraceNodeStatus.FAILED


class AgentTraceRecorder:
    """业务代码记录 Agent 执行节点的唯一入口。"""

    def __init__(
        self,
        repository: TraceRepository,
        exporter: TraceExportClient,
        detail_gateway: TraceDetailGateway | None = None,
    ) -> None:
        self._repository = repository
        self._exporter = exporter
        self._detail_gateway = detail_gateway

    def current_run_id(self) -> int | None:
        """返回当前调用上下文所属 Run，供下层服务建立子节点。"""

        frame = _active_frame.get()
        return frame.node.run_id if frame is not None else None

    @contextmanager
    def node(
        self,
        spec: TraceNodeSpec,
        *,
        input_data: Mapping[str, Any] | None = None,
        input_detail: Mapping[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> Iterator[TraceNodeHandle]:
        """记录一个节点；Trace 基础设施失败不改变业务操作结果。"""

        node_started_at = started_at or datetime.now()
        parent_frame = _active_frame.get()
        if parent_frame is not None and parent_frame.node.run_id != spec.run_id:
            self._record_trace_failure(
                spec.run_id,
                spec.name,
                TraceContractError("TRACE_CONTEXT_RUN_MISMATCH"),
            )
            parent_frame = None

        root = (
            parent_frame.node
            if parent_frame is not None
            else self._ensure_root(spec, node_started_at)
        )
        node_ref = self._start_node(
            spec,
            parent=root,
            started_at=node_started_at,
            input_data=redact_trace_mapping(input_data),
        )
        chat_id = _positive_int(spec.attributes.get("app.chat.id")) or (
            parent_frame.chat_id if parent_frame is not None else None
        )
        record_id = _positive_int(spec.attributes.get("app.record.id")) or (
            parent_frame.record_id if parent_frame is not None else None
        )
        active_ref = node_ref or root

        export_attributes = {
            **spec.attributes,
            "app.trace.node.name": spec.name,
            "app.trace.node.type": spec.node_type.value,
        }
        if node_ref is not None:
            export_attributes["app.trace.node.id"] = node_ref.id
        with self._exporter.span(spec.name, export_attributes) as export_span:
            handle = TraceNodeHandle(export_span)
            input_artifact_ref = self._write_detail(
                # 节点自身未落库时不写详情，避免产生无法关联的孤立 Artifact。
                node=node_ref,
                chat_id=chat_id,
                record_id=record_id,
                side="input",
                payload=redact_trace_mapping(input_detail),
            )
            token = _active_frame.set(
                _TraceFrame(active_ref, chat_id, record_id) if active_ref else None
            )
            business_error: BaseException | None = None
            try:
                yield handle
            except BaseException as exc:
                business_error = exc
                handle.status = (
                    TraceNodeStatus.INTERRUPTED
                    if isinstance(exc, GeneratorExit)
                    else TraceNodeStatus.FAILED
                )
                handle.error_category = exc.__class__.__name__
                handle.error = str(exc) or exc.__class__.__name__
                raise
            finally:
                _active_frame.reset(token)
                if node_ref is not None:
                    output_artifact_ref = self._write_detail(
                        node=node_ref,
                        chat_id=chat_id,
                        record_id=record_id,
                        side="output",
                        payload=handle.output_detail or {},
                    )
                    trace_id, span_id = export_span.identifiers()
                    self._finish_node(
                        node_ref,
                        handle,
                        input_artifact_ref=input_artifact_ref,
                        output_artifact_ref=output_artifact_ref,
                        trace_id=trace_id,
                        span_id=span_id,
                    )
                if business_error is None and handle.status is TraceNodeStatus.FAILED:
                    logger.debug("Trace 节点由业务显式标记失败: %s", spec.name)

    def _ensure_root(
        self,
        spec: TraceNodeSpec,
        started_at: datetime,
    ) -> TraceNodeRef | None:
        try:
            root, _ = self._repository.ensure_run_root(
                TraceNodeStartInput(
                    run_id=spec.run_id,
                    parent_id=None,
                    node_key="run",
                    node_type=TraceNodeType.RUN,
                    name="agent_run",
                    display_name="Agent Run",
                    started_at=started_at,
                    input_summary={"run_id": spec.run_id},
                    metadata=sanitize_attributes(spec.attributes),
                )
            )
            return root
        except TraceError as exc:
            self._record_trace_failure(spec.run_id, "agent_run", exc)
            return None

    def _start_node(
        self,
        spec: TraceNodeSpec,
        *,
        parent: TraceNodeRef | None,
        started_at: datetime,
        input_data: dict[str, Any],
    ) -> TraceNodeRef | None:
        if parent is None:
            self._record_trace_failure(
                spec.run_id,
                spec.name,
                TraceContractError("TRACE_ROOT_UNAVAILABLE"),
            )
            return None
        try:
            return self._repository.start_node(
                TraceNodeStartInput(
                    run_id=spec.run_id,
                    parent_id=parent.id,
                    node_key=spec.node_key,
                    node_type=spec.node_type,
                    name=spec.name,
                    display_name=spec.display_name,
                    started_at=started_at,
                    input_summary=input_data,
                    metadata=redact_trace_mapping(
                        {
                            **spec.metadata,
                            **sanitize_attributes(spec.attributes),
                        }
                    ),
                )
            )
        except TraceError as exc:
            self._record_trace_failure(spec.run_id, spec.name, exc)
            return None

    def _finish_node(
        self,
        node: TraceNodeRef,
        handle: TraceNodeHandle,
        *,
        input_artifact_ref: dict[str, Any] | None,
        output_artifact_ref: dict[str, Any] | None,
        trace_id: str | None,
        span_id: str | None,
    ) -> None:
        try:
            self._repository.finish_node(
                TraceNodeFinishInput(
                    node_id=node.id,
                    status=handle.status,
                    finished_at=datetime.now(),
                    output_summary=handle.output_summary,
                    input_artifact_ref=input_artifact_ref,
                    output_artifact_ref=output_artifact_ref,
                    state_diff=handle.state_diff,
                    token_usage=handle.token_usage,
                    error_code=handle.error_code,
                    error_category=handle.error_category,
                    error=(
                        str(redact_trace_payload(handle.error))
                        if handle.error is not None
                        else None
                    ),
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=handle.metadata,
                )
            )
        except TraceError as exc:
            self._record_trace_failure(node.run_id, node.node_key, exc)

    def _write_detail(
        self,
        *,
        node: TraceNodeRef | None,
        chat_id: int | None,
        record_id: int | None,
        side: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not payload or self._detail_gateway is None or node is None:
            return None
        if chat_id is None or record_id is None:
            self._record_trace_failure(
                node.run_id,
                node.node_key,
                TraceContractError("TRACE_DETAIL_OWNERSHIP_REQUIRED"),
            )
            return None
        try:
            return self._detail_gateway.write(
                TraceDetailWriteInput(
                    run_id=node.run_id,
                    node_id=node.id,
                    chat_id=chat_id,
                    record_id=record_id,
                    side=side,
                    payload=payload,
                )
            )
        except TraceError as exc:
            self._record_trace_failure(node.run_id, node.node_key, exc)
            return None

    def _record_trace_failure(
        self,
        run_id: int,
        node_name: str,
        error: TraceError,
    ) -> None:
        logger.error(
            "Agent Trace 写入失败: run_id=%s node=%s error=%s",
            run_id,
            node_name,
            error,
        )
        try:
            self._repository.mark_run_partial(run_id, lost_nodes=1)
        except TraceError:
            logger.exception(
                "Agent Trace 根节点无法标记为 partial: run_id=%s",
                run_id,
            )


class DisabledTraceRepository:
    """测试显式选择的不持久化 Repository，仍保留父子上下文。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_id = 1
        self._roots: dict[int, TraceNodeRef] = {}

    def ensure_run_root(
        self,
        data: TraceNodeStartInput,
    ) -> tuple[TraceNodeRef, bool]:
        with self._lock:
            existing = self._roots.get(data.run_id)
            if existing is not None:
                return existing, False
            root = self._new_ref(data)
            self._roots[data.run_id] = root
            return root, True

    def start_node(self, data: TraceNodeStartInput) -> TraceNodeRef:
        with self._lock:
            return self._new_ref(data)

    def finish_node(self, data: TraceNodeFinishInput) -> None:
        return None

    def mark_run_partial(self, run_id: int, *, lost_nodes: int = 1) -> None:
        return None

    def _new_ref(self, data: TraceNodeStartInput) -> TraceNodeRef:
        node_id = self._next_id
        self._next_id += 1
        return TraceNodeRef(
            id=node_id,
            run_id=data.run_id,
            parent_id=data.parent_id,
            node_key=data.node_key or f"{data.name}:{node_id}",
            sequence=node_id,
            node_type=data.node_type,
        )


class DisabledAgentTraceRecorder(AgentTraceRecorder):
    """测试显式使用的无持久化、无导出 Recorder。"""

    def __init__(self) -> None:
        super().__init__(DisabledTraceRepository(), DisabledTraceExporter())


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


__all__ = [
    "AgentTraceRecorder",
    "DisabledAgentTraceRecorder",
    "DisabledTraceRepository",
    "TraceNodeHandle",
]
