"""Agent Trace 的稳定技术端口。"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol

from apps.trace.models import (
    TraceDetailWriteInput,
    TraceNodeFinishInput,
    TraceNodeRef,
    TraceNodeStartInput,
    TraceRunFinishInput,
)


class TraceRepository(Protocol):
    """持久化调用树的最小仓储端口。"""

    def ensure_run_root(
        self,
        data: TraceNodeStartInput,
    ) -> tuple[TraceNodeRef, bool]: ...

    def start_node(self, data: TraceNodeStartInput) -> TraceNodeRef: ...

    def finish_node(self, data: TraceNodeFinishInput) -> None: ...

    def finish_run_root(self, data: TraceRunFinishInput) -> None: ...

    def mark_run_partial(self, run_id: int, *, lost_nodes: int = 1) -> None: ...


class TraceDetailGateway(Protocol):
    """写入单节点脱敏大详情的 Artifact 防腐端口。"""

    def write(self, data: TraceDetailWriteInput) -> dict[str, Any]: ...


class TraceExportSpan(Protocol):
    """Recorder 允许使用的最小外部 Span 接口。"""

    def set_attribute(self, name: str, value: Any) -> None: ...

    def identifiers(self) -> tuple[str | None, str | None]: ...


class TraceExportClient(Protocol):
    """与具体 OpenTelemetry SDK 解耦的可选客户端端口。"""

    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Any: ...


class DisabledTraceExportSpan:
    """OpenTelemetry 导出关闭时使用的空实现。"""

    def set_attribute(self, name: str, value: Any) -> None:
        return None

    def identifiers(self) -> tuple[None, None]:
        return None, None


class DisabledTraceExporter:
    """不加载 OpenTelemetry 依赖的导出器。"""

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceExportSpan]:
        yield DisabledTraceExportSpan()


__all__ = [
    "DisabledTraceExportSpan",
    "DisabledTraceExporter",
    "TraceDetailGateway",
    "TraceExportClient",
    "TraceExportSpan",
    "TraceRepository",
]
