"""Agent 可观测性 Trace 的稳定接口。"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol


class AgentSpan(Protocol):
    """业务代码允许使用的最小 span 接口。"""

    def set_attribute(self, name: str, value: Any) -> None: ...


class AgentTracer(Protocol):
    """与具体 OpenTelemetry SDK 解耦的 tracer 接口。"""

    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Any: ...


class DisabledAgentSpan:
    """tracing 关闭时使用的空实现。"""

    def set_attribute(self, name: str, value: Any) -> None:
        return None


class DisabledAgentTracer:
    """不加载 OpenTelemetry 依赖的 tracer。"""

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[AgentSpan]:
        yield DisabledAgentSpan()


__all__ = ["AgentSpan", "AgentTracer", "DisabledAgentSpan", "DisabledAgentTracer"]
