"""工具调用分批：只读且 concurrency_safe 的连续调用可并行。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any

from apps.tool.base import Tool
from apps.tool.output import ToolOutput


@dataclass(frozen=True)
class ToolCallRequest:
    name: str
    args: dict[str, Any]
    call_id: str


def is_terminal_tool(name: str) -> bool:
    return name in {"clarify", "finish"}


def batch_tool_calls(
    calls: list[ToolCallRequest],
    resolve_tool: Callable[[str], Tool | None],
) -> list[list[ToolCallRequest]]:
    """按 is_concurrency_safe 把调用切成串行批次。

    - 终止动作（clarify/finish）单独成批，强制串行
    - 连续 concurrency_safe 工具合并为一批（可并行）
    - 其余工具各自成批
    """

    batches: list[list[ToolCallRequest]] = []
    current: list[ToolCallRequest] = []

    def flush() -> None:
        nonlocal current
        if current:
            batches.append(current)
            current = []

    for call in calls:
        tool = resolve_tool(call.name)
        concurrent = (
            tool is not None
            and tool.is_concurrency_safe
            and not is_terminal_tool(call.name)
        )
        if is_terminal_tool(call.name) or not concurrent:
            flush()
            batches.append([call])
            continue
        if current and not _batch_is_concurrent(current, resolve_tool):
            flush()
        current.append(call)
    flush()
    return batches


def _batch_is_concurrent(
    batch: list[ToolCallRequest],
    resolve_tool: Callable[[str], Tool | None],
) -> bool:
    if not batch:
        return False
    for call in batch:
        tool = resolve_tool(call.name)
        if tool is None or not tool.is_concurrency_safe or is_terminal_tool(call.name):
            return False
    return True


def execute_tool_batch(
    batch: list[ToolCallRequest],
    execute: Callable[[str, dict[str, Any]], ToolOutput],
    *,
    max_workers: int = 4,
) -> list[tuple[ToolCallRequest, ToolOutput]]:
    """执行一批工具调用；长度>1 时并行，结果按原顺序返回。"""

    if len(batch) <= 1:
        return [(call, execute(call.name, call.args)) for call in batch]

    workers = max(1, min(max_workers, len(batch)))
    results: dict[int, ToolOutput] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # 每个并行工具复制当前上下文，确保 OTEL 父 span 等 contextvars 不丢失。
        futures = {
            pool.submit(copy_context().run, execute, call.name, call.args): index
            for index, call in enumerate(batch)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # 兜底：并行线程内未捕获异常
                results[index] = ToolOutput.error(
                    f"工具并行执行异常: {exc}",
                    error_code="tool_parallel_exception",
                )
    return [(batch[index], results[index]) for index in range(len(batch))]
