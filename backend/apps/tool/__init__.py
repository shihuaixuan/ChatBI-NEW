"""通用工具运行时内核（弱仪式、无业务依赖）。

只提供 Tool / Registry / Output / 消息收口 / 预算原语等横切能力。
ChatBI 等领域工具实现不得放入本包。
"""

from apps.tool.base import (
    Tool,
    ToolConcurrency,
    ToolExecutionPolicy,
    ToolSideEffect,
    json_summary,
    truncate_summary,
)
from apps.tool.budget import BudgetGuard, BudgetVerdict
from apps.tool.concurrency import (
    ToolBatchExecutionError,
    batch_tool_calls,
    execute_tool_batch,
)
from apps.tool.context import (
    NeverCancelled,
    ToolCall,
    ToolCallContext,
    current_tool_call_context,
    effective_timeout_seconds,
)
from apps.tool.definition import ToolAnnotations, ToolDefinition
from apps.tool.middleware import (
    LatencyMiddleware,
    apply_middleware,
    default_middlewares,
)
from apps.tool.registry import ToolRegistry
from apps.tool.result import (
    EmptyToolData,
    RetryAdvice,
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)

__all__ = [
    "BudgetGuard",
    "BudgetVerdict",
    "EmptyToolData",
    "LatencyMiddleware",
    "NeverCancelled",
    "Tool",
    "ToolAnnotations",
    "ToolBatchExecutionError",
    "ToolCall",
    "ToolCallContext",
    "ToolConcurrency",
    "ToolDefinition",
    "ToolErrorCategory",
    "ToolExecutionPolicy",
    "ToolRegistry",
    "ToolResult",
    "ToolSideEffect",
    "ToolStatus",
    "RetryAdvice",
    "apply_middleware",
    "batch_tool_calls",
    "default_middlewares",
    "execute_tool_batch",
    "current_tool_call_context",
    "effective_timeout_seconds",
    "json_summary",
    "truncate_summary",
]
