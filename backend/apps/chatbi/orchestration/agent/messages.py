"""Agent 自有消息协议及消息历史处理。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from apps.tool import ToolCall, ToolResult

FOLDED_PLACEHOLDER = "（此前的工具结果已折叠归档，如需请重新调用工具）"
OFFLOAD_MARKER_RE = re.compile(r"\[offload_ref=([^\]]+)\]")
DEFAULT_OFFLOAD_CHARS = 2500
_DEFAULT_SKIP_CONTENT = "skipped: run ended before this tool executed"


class AgentMessageRole(StrEnum):
    """模型消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(BaseModel):
    """不依赖模型厂商的 Agent 消息。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentMessageRole
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def system(cls, content: str) -> AgentMessage:
        return cls(role=AgentMessageRole.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> AgentMessage:
        return cls(role=AgentMessageRole.USER, content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        *,
        tool_calls: list[ToolCall] | None = None,
        reasoning_content: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> AgentMessage:
        return cls(
            role=AgentMessageRole.ASSISTANT,
            content=content,
            tool_calls=list(tool_calls or []),
            reasoning_content=reasoning_content,
            usage=dict(usage or {}),
        )

    @classmethod
    def tool(cls, content: str, tool_call_id: str) -> AgentMessage:
        return cls(
            role=AgentMessageRole.TOOL,
            content=content,
            tool_call_id=tool_call_id,
        )


@dataclass(frozen=True, slots=True)
class ModelDecision:
    """模型适配器归一化后的一轮决策。"""

    message: AgentMessage
    tool_calls: list[ToolCall]
    usage: dict[str, Any]


def restore_messages(snapshot: list[dict[str, Any]]) -> list[AgentMessage]:
    """从项目自有 JSON 快照恢复消息；旧协议必须在发布前单独处理。"""

    return [AgentMessage.model_validate(item) for item in snapshot]


def close_unfinished_tool_calls(
    messages: list[AgentMessage],
    *,
    content: str = _DEFAULT_SKIP_CONTENT,
    exclude_ids: set[str] | None = None,
) -> list[AgentMessage]:
    """补齐最后一条助手消息中尚未闭合的 Tool Call。"""

    exclude = exclude_ids or set()
    last_assistant_index = -1
    pending_ids: list[str] = []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == AgentMessageRole.ASSISTANT and message.tool_calls:
            last_assistant_index = index
            pending_ids = [call.call_id for call in message.tool_calls if call.call_id]
            break
    if last_assistant_index < 0 or not pending_ids:
        return []

    existing = {
        message.tool_call_id
        for message in messages[last_assistant_index + 1 :]
        if message.tool_call_id
    }
    appended: list[AgentMessage] = []
    for call_id in pending_ids:
        if call_id in existing or call_id in exclude:
            continue
        message = AgentMessage.tool(content, call_id)
        messages.append(message)
        appended.append(message)
        existing.add(call_id)
    return appended


def extract_offload_ref(content: str | None) -> str | None:
    if not content:
        return None
    match = OFFLOAD_MARKER_RE.search(content)
    return match.group(1) if match else None


def make_fold_placeholder(offload_ref: str | None = None) -> str:
    if offload_ref:
        return (
            f"（此前的工具结果已折叠归档，offload_ref={offload_ref}，"
            "如需请重新调用工具）"
        )
    return FOLDED_PLACEHOLDER


def format_tool_message_content(summary: str, *, offload_ref: str | None = None) -> str:
    """生成写入 Tool 消息的正文。"""

    if offload_ref and f"[offload_ref={offload_ref}]" not in summary:
        return f"{summary}\n[offload_ref={offload_ref}]" if summary else f"[offload_ref={offload_ref}]"
    return summary


def maybe_offload_result(
    result: ToolResult[Any],
    *,
    store: dict[str, Any],
    tool_name: str,
    max_chars: int = DEFAULT_OFFLOAD_CHARS,
) -> ToolResult[Any]:
    """归档过长模型内容，结构化业务数据保持独立。"""

    offload_ref = str(result.metadata.get("offload_ref") or "") or None
    if offload_ref:
        store[offload_ref] = _offload_value(result, tool_name)
        return result
    if max_chars <= 0 or len(result.model_content) <= max_chars:
        return result

    ref = f"tool:{tool_name}:{uuid4().hex[:10]}"
    store[ref] = _offload_value(result, tool_name)
    head = result.model_content[: max(0, max_chars - 80)]
    short = (
        f"{head}\n…(已归档 offload_ref={ref}，原文 {len(result.model_content)} 字符)"
        if head
        else f"（已归档 offload_ref={ref}，原文 {len(result.model_content)} 字符）"
    )
    metadata = dict(result.metadata)
    metadata["offload_ref"] = ref
    return result.with_updates(model_content=short, metadata=metadata)


def _offload_value(result: ToolResult[Any], tool_name: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "model_content": result.model_content,
        "data": result.data.model_dump(mode="json") if result.data else None,
    }


def fold_tool_messages(
    messages: list[AgentMessage],
    max_chars: int,
    *,
    keep_recent: int = 6,
) -> None:
    """消息历史超过阈值时，就地折叠较早的 Tool 结果。"""

    if max_chars <= 0 or sum(len(message.content) for message in messages) <= max_chars:
        return
    for index, message in enumerate(messages[:-keep_recent]):
        if message.role != AgentMessageRole.TOOL:
            continue
        if message.content.startswith("（此前的工具结果已折叠归档"):
            continue
        messages[index] = message.model_copy(
            update={"content": make_fold_placeholder(extract_offload_ref(message.content))}
        )


__all__ = [
    "AgentMessage",
    "AgentMessageRole",
    "DEFAULT_OFFLOAD_CHARS",
    "FOLDED_PLACEHOLDER",
    "ModelDecision",
    "close_unfinished_tool_calls",
    "extract_offload_ref",
    "fold_tool_messages",
    "format_tool_message_content",
    "make_fold_placeholder",
    "maybe_offload_result",
    "restore_messages",
]
