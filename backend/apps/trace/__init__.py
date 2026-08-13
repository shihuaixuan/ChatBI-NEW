"""独立于产品 Event 链路的 Agent 可观测性接口。"""

from apps.trace.attributes import agent_attributes, llm_attributes, tool_attributes
from apps.trace.errors import TraceContractError, TraceError, TraceWriteError
from apps.trace.models import (
    TRACE_TERMINAL_STATUSES,
    TraceDetailWriteInput,
    TraceNodeFinishInput,
    TraceNodeRef,
    TraceNodeSpec,
    TraceNodeStartInput,
    TraceNodeStatus,
    TraceNodeType,
    TraceRunFinishInput,
)
from apps.trace.ports import (
    DisabledTraceExporter,
    TraceDetailGateway,
    TraceExportClient,
    TraceExportSpan,
    TraceRepository,
)
from apps.trace.recorder import (
    AgentTraceRecorder,
    DisabledAgentTraceRecorder,
    DisabledTraceRepository,
    TraceNodeHandle,
)
from apps.trace.setup import TraceConfig, build_trace_exporter

__all__ = [
    "AgentTraceRecorder",
    "DisabledAgentTraceRecorder",
    "DisabledTraceExporter",
    "DisabledTraceRepository",
    "TRACE_TERMINAL_STATUSES",
    "TraceConfig",
    "TraceContractError",
    "TraceDetailWriteInput",
    "TraceDetailGateway",
    "TraceError",
    "TraceExportClient",
    "TraceExportSpan",
    "TraceNodeFinishInput",
    "TraceNodeHandle",
    "TraceNodeRef",
    "TraceNodeSpec",
    "TraceNodeStartInput",
    "TraceNodeStatus",
    "TraceNodeType",
    "TraceRunFinishInput",
    "TraceRepository",
    "TraceWriteError",
    "agent_attributes",
    "build_trace_exporter",
    "llm_attributes",
    "tool_attributes",
]
