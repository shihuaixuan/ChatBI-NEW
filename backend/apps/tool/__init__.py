"""通用工具运行时内核（弱仪式、无业务依赖）。

只提供 Tool / Registry / Output / 消息收口 / 预算原语等横切能力。
ChatBI 等领域工具实现不得放入本包。
"""

from apps.tool.base import Tool, json_summary, truncate_summary
from apps.tool.budget import BudgetGuard, BudgetVerdict
from apps.tool.concurrency import (
    ToolCallRequest,
    batch_tool_calls,
    execute_tool_batch,
    is_terminal_tool,
)
from apps.tool.messages import (
    DEFAULT_OFFLOAD_CHARS,
    FOLDED_PLACEHOLDER,
    close_unfinished_tool_calls,
    extract_offload_ref,
    fold_tool_messages,
    format_tool_message_content,
    make_fold_placeholder,
    maybe_offload_output,
)
from apps.tool.middleware import (
    ErrorNormalizeMiddleware,
    LatencyMiddleware,
    TimeoutMiddleware,
    apply_middleware,
    default_middlewares,
)
from apps.tool.output import ToolOutput, ToolStatus
from apps.tool.registry import ToolRegistry

__all__ = [
    "BudgetGuard",
    "BudgetVerdict",
    "DEFAULT_OFFLOAD_CHARS",
    "ErrorNormalizeMiddleware",
    "FOLDED_PLACEHOLDER",
    "LatencyMiddleware",
    "TimeoutMiddleware",
    "Tool",
    "ToolCallRequest",
    "ToolOutput",
    "ToolRegistry",
    "ToolStatus",
    "apply_middleware",
    "batch_tool_calls",
    "close_unfinished_tool_calls",
    "default_middlewares",
    "execute_tool_batch",
    "extract_offload_ref",
    "fold_tool_messages",
    "format_tool_message_content",
    "is_terminal_tool",
    "json_summary",
    "make_fold_placeholder",
    "maybe_offload_output",
    "truncate_summary",
]
