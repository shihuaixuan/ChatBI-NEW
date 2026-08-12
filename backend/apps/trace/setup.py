"""OpenTelemetry tracer 的惰性装配。"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Any

from apps.trace.attributes import sanitize_attributes
from apps.trace.ports import (
    DisabledTraceExporter,
    DisabledTraceExportSpan,
    TraceExportClient,
    TraceExportSpan,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool = False
    sample_rate: float = 0.0
    service_name: str = "numora-agent"
    endpoint: str = ""


class OpenTelemetryTraceExporter:
    """把 Recorder 的导出端口适配到 OpenTelemetry。"""

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceExportSpan]:
        with self._tracer.start_as_current_span(
            name,
            attributes=sanitize_attributes(attributes),
            record_exception=True,
            set_status_on_exception=True,
        ) as span:
            yield OpenTelemetryTraceExportSpan(span)


class ResilientTraceExporter:
    """隔离运行期 OpenTelemetry 故障，避免影响持久化 Trace。"""

    def __init__(self, delegate: TraceExportClient) -> None:
        self._delegate = delegate

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceExportSpan]:
        try:
            manager = self._delegate.span(name, attributes)
            span = manager.__enter__()
        except Exception:
            logger.exception("OpenTelemetry span 启动失败: %s", name)
            yield DisabledTraceExportSpan()
            return
        try:
            yield ResilientTraceExportSpan(span, name)
        except BaseException as exc:
            try:
                suppress = manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                logger.exception("OpenTelemetry span 异常收口失败: %s", name)
                suppress = False
            if not suppress:
                raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.exception("OpenTelemetry span 收口失败: %s", name)


class ResilientTraceExportSpan:
    """隔离属性写入异常。"""

    def __init__(self, delegate: TraceExportSpan, name: str) -> None:
        self._delegate = delegate
        self._name = name

    def set_attribute(self, name: str, value: Any) -> None:
        try:
            self._delegate.set_attribute(name, value)
        except Exception:
            logger.exception("OpenTelemetry span 属性写入失败: %s", self._name)

    def identifiers(self) -> tuple[str | None, str | None]:
        try:
            return self._delegate.identifiers()
        except Exception:
            logger.exception("OpenTelemetry span 标识读取失败: %s", self._name)
            return None, None


class OpenTelemetryTraceExportSpan:
    """为 OpenTelemetry Span 补充稳定的标识读取接口。"""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, name: str, value: Any) -> None:
        self._span.set_attribute(name, value)

    def identifiers(self) -> tuple[str | None, str | None]:
        context = self._span.get_span_context()
        if not context or not context.is_valid:
            return None, None
        return f"{context.trace_id:032x}", f"{context.span_id:016x}"


@lru_cache(maxsize=8)
def build_trace_exporter(config: TraceConfig) -> TraceExportClient:
    """按配置构造导出器；关闭和零采样时完全不加载 OTEL。"""

    if not config.enabled or config.sample_rate <= 0:
        return DisabledTraceExporter()
    if not 0 < config.sample_rate <= 1:
        raise ValueError("AGENT_TRACING_SAMPLE_RATE must be between 0 and 1")

    runtime = _load_otel_runtime()
    resource = runtime["Resource"].create({"service.name": config.service_name})
    sampler = runtime["ParentBased"](runtime["TraceIdRatioBased"](config.sample_rate))
    provider = runtime["TracerProvider"](resource=resource, sampler=sampler)
    exporter_kwargs = {"endpoint": config.endpoint} if config.endpoint else {}
    exporter = runtime["OTLPSpanExporter"](**exporter_kwargs)
    provider.add_span_processor(runtime["BatchSpanProcessor"](exporter))
    tracer = provider.get_tracer("numora.apps.trace")
    return ResilientTraceExporter(OpenTelemetryTraceExporter(tracer))


def _load_otel_runtime() -> dict[str, Any]:
    """仅在 tracing 启用时加载 SDK；缺失时抛出明确错误。"""

    try:
        exporter_module = import_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        resource_module = import_module("opentelemetry.sdk.resources")
        trace_module = import_module("opentelemetry.sdk.trace")
        export_module = import_module("opentelemetry.sdk.trace.export")
        sampling_module = import_module("opentelemetry.sdk.trace.sampling")
    except ImportError as exc:
        raise ImportError(
            "Agent tracing 已启用，但缺少 OpenTelemetry 依赖；"
            "请安装项目的 observability 可选依赖"
        ) from exc
    return {
        "OTLPSpanExporter": exporter_module.OTLPSpanExporter,
        "Resource": resource_module.Resource,
        "TracerProvider": trace_module.TracerProvider,
        "BatchSpanProcessor": export_module.BatchSpanProcessor,
        "ParentBased": sampling_module.ParentBased,
        "TraceIdRatioBased": sampling_module.TraceIdRatioBased,
    }


__all__ = [
    "OpenTelemetryTraceExportSpan",
    "OpenTelemetryTraceExporter",
    "ResilientTraceExportSpan",
    "ResilientTraceExporter",
    "TraceConfig",
    "build_trace_exporter",
]
