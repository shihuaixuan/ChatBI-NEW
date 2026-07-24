"""工具相关消息历史辅助：dangling 收口、offload 引用与上下文折叠。"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

FOLDED_PLACEHOLDER = "（此前的工具结果已折叠归档，如需请重新调用工具）"
OFFLOAD_MARKER_RE = re.compile(r"\[offload_ref=([^\]]+)\]")
_DEFAULT_SKIP_CONTENT = "skipped: run ended before this tool executed"
DEFAULT_OFFLOAD_CHARS = 2500


def close_unfinished_tool_calls(
    messages: list[Any],
    *,
    content: str = _DEFAULT_SKIP_CONTENT,
    exclude_ids: set[str] | None = None,
) -> list[Any]:
    """为未配对的 tool_calls 补 synthetic ToolMessage，避免消息历史非法。

    从末尾向前找最近一条带 tool_calls 的 assistant 消息，补齐其后缺失的
    tool_call_id。``exclude_ids`` 用于 clarify 挂起：该 call_id 需留给用户
    答案注入，不能提前闭合。
    """

    try:
        from langchain_core.messages import ToolMessage
    except ImportError as exc:  # pragma: no cover - 运行时必有 langchain
        raise RuntimeError("langchain_core is required for tool message helpers") from exc

    exclude = exclude_ids or set()
    last_ai_index = -1
    pending_ids: list[str] = []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if tool_calls:
            last_ai_index = index
            pending_ids = [
                str(call.get("id") or "")
                for call in tool_calls
                if call.get("id")
            ]
            break

    if last_ai_index < 0 or not pending_ids:
        return []

    existing: set[str] = set()
    for message in messages[last_ai_index + 1 :]:
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            existing.add(str(tool_call_id))

    appended: list[Any] = []
    for call_id in pending_ids:
        if not call_id or call_id in existing or call_id in exclude:
            continue
        tool_message = ToolMessage(content=content, tool_call_id=call_id)
        messages.append(tool_message)
        appended.append(tool_message)
        existing.add(call_id)
    return appended


def extract_offload_ref(content: str | None) -> str | None:
    if not content:
        return None
    match = OFFLOAD_MARKER_RE.search(str(content))
    return match.group(1) if match else None


def make_fold_placeholder(offload_ref: str | None = None) -> str:
    if offload_ref:
        return (
            f"（此前的工具结果已折叠归档，offload_ref={offload_ref}，"
            "如需请重新调用工具）"
        )
    return FOLDED_PLACEHOLDER


def format_tool_message_content(
    summary: str,
    *,
    offload_ref: str | None = None,
) -> str:
    """生成写入 ToolMessage 的正文；若有归档引用则附稳定 marker。"""

    text = summary or ""
    if offload_ref and f"[offload_ref={offload_ref}]" not in text:
        if text:
            return f"{text}\n[offload_ref={offload_ref}]"
        return f"[offload_ref={offload_ref}]"
    return text


def maybe_offload_output(
    output: Any,
    *,
    store: dict[str, Any],
    tool_name: str,
    max_chars: int = DEFAULT_OFFLOAD_CHARS,
) -> Any:
    """大 summary 自动归档：完整内容进 store，摘要保留短视图 + offload_ref。"""

    from apps.tool.output import ToolOutput

    if not isinstance(output, ToolOutput):
        return output
    if output.offload_ref:
        store[output.offload_ref] = {
            "tool_name": tool_name,
            "summary": output.summary,
            "payload": output.payload,
        }
        return output
    if max_chars <= 0 or len(output.summary or "") <= max_chars:
        return output

    ref = f"tool:{tool_name}:{uuid4().hex[:10]}"
    store[ref] = {
        "tool_name": tool_name,
        "summary": output.summary,
        "payload": output.payload,
    }
    head = (output.summary or "")[: max(0, max_chars - 80)]
    short = (
        f"{head}\n…(已归档 offload_ref={ref}，原文 {len(output.summary)} 字符)"
        if head
        else f"（已归档 offload_ref={ref}，原文 {len(output.summary)} 字符）"
    )
    return ToolOutput(
        success=output.success,
        summary=short,
        payload=output.payload,
        error_code=output.error_code,
        status=output.status,
        offload_ref=ref,
    )


def fold_tool_messages(
    messages: list[Any],
    max_chars: int,
    *,
    keep_recent: int = 6,
    placeholder: str = FOLDED_PLACEHOLDER,
) -> None:
    """消息历史超过阈值时，把较早的工具结果折叠为占位符（就地修改）。

    若原文含 offload_ref，折叠占位会保留该引用，便于后续按需回源。
    """

    try:
        from langchain_core.messages import ToolMessage
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("langchain_core is required for tool message helpers") from exc

    if max_chars <= 0:
        return
    total = sum(len(str(getattr(message, "content", ""))) for message in messages)
    if total <= max_chars:
        return
    for message in messages[:-keep_recent]:
        if not isinstance(message, ToolMessage):
            continue
        content = str(message.content or "")
        if content == placeholder or content.startswith("（此前的工具结果已折叠归档"):
            continue
        ref = extract_offload_ref(content)
        message.content = make_fold_placeholder(ref)
