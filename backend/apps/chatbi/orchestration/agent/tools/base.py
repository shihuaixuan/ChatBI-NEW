"""Agent 工具基座：ChatBI 执行上下文 + 领域工具基类。

通用运行时（Tool/ToolResult/Registry）在 apps.tool。
本模块只保留问数执行上下文与服务端口。
"""

from __future__ import annotations

from typing import Any

from apps.chatbi.services.ports import (
    AgentToolContext,
    AgentToolContextServices,
)
from apps.tool import Tool


class AgentTool(Tool[AgentToolContext, Any, Any]):
    """ChatBI 领域工具基类；继承通用 Tool 协议。"""


__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolContextServices",
]
