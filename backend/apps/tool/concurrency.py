"""工具调用分批：只读且 concurrency_safe 的连续调用可并行。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import Any

from apps.tool.base import Tool, ToolConcurrency
from apps.tool.context import ToolCall
from apps.tool.result import ToolResult


def batch_tool_calls(
    calls: list[ToolCall],
    resolve_tool: Callable[[str], Tool | None],
) -> list[list[ToolCall]]:
    """只按 Tool 执行属性切分串行和并行批次。

    - 连续 parallel_safe 工具合并为一批
    - 其余工具各自成批
    """

    batches: list[list[ToolCall]] = []
    current: list[ToolCall] = []

    def flush() -> None:
        nonlocal current
        if current:
            batches.append(current)
            current = []

    for call in calls:
        tool = resolve_tool(call.name)
        concurrent = (
            tool is not None
            and tool.execution.concurrency == ToolConcurrency.PARALLEL_SAFE
        )
        if not concurrent:
            flush()
            batches.append([call])
            continue
        if current and not _batch_is_concurrent(current, resolve_tool):
            flush()
        current.append(call)
    flush()
    return batches


def _batch_is_concurrent(
    batch: list[ToolCall],
    resolve_tool: Callable[[str], Tool | None],
) -> bool:
    if not batch:
        return False
    for call in batch:
        tool = resolve_tool(call.name)
        if (
            tool is None
            or tool.execution.concurrency != ToolConcurrency.PARALLEL_SAFE
        ):
            return False
    return True


def execute_tool_batch(
    batch: list[ToolCall],
    execute: Callable[[ToolCall], ToolResult[Any]],
    *,
    max_workers: int = 4,
) -> list[tuple[ToolCall, ToolResult[Any]]]:
    """执行一批工具调用；长度>1 时并行，结果按原顺序返回。"""

    if len(batch) <= 1:
        return [(call, execute(call)) for call in batch]

    workers = max(1, min(max_workers, len(batch)))
    results: dict[int, ToolResult[Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # 每个并行工具复制当前上下文，确保 OTEL 父 span 等 contextvars 不丢失。
        futures = {
            pool.submit(copy_context().run, execute, call): index
            for index, call in enumerate(batch)
        }
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
    return [(batch[index], results[index]) for index in range(len(batch))]
