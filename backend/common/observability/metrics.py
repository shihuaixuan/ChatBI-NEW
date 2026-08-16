"""ChatBI 指标层：关闭时无外部依赖，开启时使用 OpenTelemetry Metrics。"""

from __future__ import annotations

import time
from importlib import import_module
from typing import Any

from common.core.config import settings


class MetricsRecorder:
    """统一指标写入端口，业务层不直接依赖 OTEL SDK。"""

    def __init__(self, meter: Any | None = None) -> None:
        self._meter = meter
        if meter is None:
            self._run_counter = None
            self._stage_histogram = None
            self._token_counter = None
            self._outcome_counter = None
            return
        self._run_counter = meter.create_counter(
            "chatbi.runs", unit="{run}", description="ChatBI Run 数量"
        )
        self._stage_histogram = meter.create_histogram(
            "chatbi.stage.duration", unit="ms", description="阶段耗时"
        )
        self._token_counter = meter.create_counter(
            "chatbi.tokens", unit="{token}", description="模型 token 用量"
        )
        self._outcome_counter = meter.create_counter(
            "chatbi.outcomes", unit="{event}", description="澄清、拒答和兜底结果"
        )

    @property
    def enabled(self) -> bool:
        return self._meter is not None

    def record_run(self, *, mode: str, status: str = "started") -> None:
        if self._run_counter is not None:
            self._run_counter.add(1, {"mode": mode, "status": status})

    def observe_stage(self, stage: str, duration_ms: float, *, status: str = "ok") -> None:
        if self._stage_histogram is not None:
            self._stage_histogram.record(
                max(float(duration_ms), 0.0), {"stage": stage, "status": status}
            )

    def record_tokens(self, count: int, *, stage: str, mode: str | None = None) -> None:
        if self._token_counter is not None and count > 0:
            attributes = {"stage": stage}
            if mode:
                attributes["mode"] = mode
            self._token_counter.add(count, attributes)

    def record_outcome(self, outcome: str, *, mode: str | None = None) -> None:
        if self._outcome_counter is not None:
            attributes = {"outcome": outcome}
            if mode:
                attributes["mode"] = mode
            self._outcome_counter.add(1, attributes)

    def time_stage(self, stage: str, *, mode: str | None = None) -> StageTimer:
        return StageTimer(self, stage, mode=mode)


class StageTimer:
    """阶段耗时上下文，异常状态同样会被记录。"""

    def __init__(self, recorder: MetricsRecorder, stage: str, *, mode: str | None) -> None:
        self._recorder = recorder
        self._stage = stage
        self._mode = mode
        self._started = 0.0
        self._status = "ok"

    def __enter__(self) -> StageTimer:
        self._started = time.perf_counter()
        return self

    def mark_failed(self) -> None:
        self._status = "error"

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None:
            self._status = "error"
        self._recorder.observe_stage(
            self._stage,
            (time.perf_counter() - self._started) * 1000,
            status=self._status,
        )


class DisabledMetricsRecorder(MetricsRecorder):
    """关闭指标时的显式存根，避免导入失败对象被赋值为 None。"""

    def __init__(self) -> None:
        super().__init__(None)


def build_metrics_recorder(*, enabled: bool | None = None) -> MetricsRecorder:
    """按配置惰性加载 OTEL；依赖缺失时抛出明确 ImportError。"""

    is_enabled = settings.OTEL_METRICS_ENABLED if enabled is None else enabled
    if not is_enabled:
        return DisabledMetricsRecorder()
    runtime = _load_otel_metrics_runtime()
    resource = runtime["Resource"].create(
        {"service.name": settings.OTEL_METRICS_SERVICE_NAME}
    )
    exporter_kwargs = (
        {"endpoint": settings.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT}
        if settings.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
        else {}
    )
    exporter = runtime["OTLPMetricExporter"](**exporter_kwargs)
    reader = runtime["PeriodicExportingMetricReader"](exporter)
    provider = runtime["MeterProvider"](resource=resource, metric_readers=[reader])
    meter = provider.get_meter("numora.apps.chatbi")
    return MetricsRecorder(meter)


def _load_otel_metrics_runtime() -> dict[str, Any]:
    """读取指标 SDK 的最小运行时，失败时保留明确依赖错误。"""

    try:
        metrics_api = import_module("opentelemetry.metrics")
        exporter_module = import_module(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter"
        )
        resource_module = import_module("opentelemetry.sdk.resources")
        sdk_module = import_module("opentelemetry.sdk.metrics")
        reader_module = import_module("opentelemetry.sdk.metrics.export")
    except ImportError as exc:
        raise ImportError(
            "OTEL metrics 已启用，但缺少 OpenTelemetry Metrics 依赖；"
            "请安装项目的 observability 可选依赖"
        ) from exc
    return {
        "MeterProvider": sdk_module.MeterProvider,
        "PeriodicExportingMetricReader": reader_module.PeriodicExportingMetricReader,
        "OTLPMetricExporter": exporter_module.OTLPMetricExporter,
        "Resource": resource_module.Resource,
        "metrics_api": metrics_api,
    }


__all__ = [
    "DisabledMetricsRecorder",
    "MetricsRecorder",
    "StageTimer",
    "build_metrics_recorder",
]
