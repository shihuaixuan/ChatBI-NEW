"""工具调用分批：只读且 concurrency_safe 的连续调用可并行。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from typing import Any

from apps.tool.base import Tool, ToolConcurrency
from apps.tool.context import CancellationSignal, ToolCall
from apps.tool.result import ToolResult, ToolStatus


class ToolBatchExecutionError(RuntimeError):
    """并发批次包含未声明异常，同时保留每个调用的实际执行结果。"""

    def __init__(
        self,
        outcomes: list[tuple[ToolCall, ToolResult[Any] | BaseException]],
        cause: BaseException,
    ) -> None:
        super().__init__("TOOL_BATCH_UNDECLARED_EXCEPTION")
        self.outcomes = outcomes
        self.cause = cause


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
    cancellation: CancellationSignal | None = None,
) -> list[tuple[ToolCall, ToolResult[Any]]]:
    """执行一批工具调用；长度>1 时并行，结果按原顺序返回。"""

    if len(batch) <= 1:
        if cancellation is not None and cancellation.is_cancelled():
            return [
                (
                    batch[0],
                    ToolResult.interrupted(
                        "用户已请求取消，工具未开始执行。",
                        error_code="tool_cancelled_before_start",
                        metadata={"underlying_operation_started": False},
                    ),
                )
            ] if batch else []
        try:
            result = [(call, execute(call)) for call in batch]
        except Exception as exc:
            raise ToolBatchExecutionError([(batch[0], exc)], exc) from exc
        if cancellation is not None and cancellation.is_cancelled():
            return [
                (
                    call,
                    _interrupt_completed_result(outcome),
                )
                for call, outcome in result
            ]
        return result

    workers = max(1, min(max_workers, len(batch)))
    outcomes: dict[int, ToolResult[Any] | BaseException] = {}
    cancellation_requested = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # 每个并行工具复制当前上下文，确保 OTEL 父 span 等 contextvars 不丢失。
        futures = {}
        for index, call in enumerate(batch):
            if cancellation is not None and cancellation.is_cancelled():
                cancellation_requested = True
                break
            futures[pool.submit(copy_context().run, execute, call)] = index
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            if cancellation is not None and cancellation.is_cancelled():
                cancellation_requested = True
                # cancel() 只对尚未开始的 Future 生效；已开始的任务继续到自然返回。
                for future in pending:
                    future.cancel()
            for future in done:
                index = futures[future]
                if future.cancelled():
                    outcomes[index] = ToolResult.interrupted(
                        "用户已请求取消，工具未开始执行。",
                        error_code="tool_cancelled_before_start",
                        metadata={"underlying_operation_started": False},
                    )
                    continue
                try:
                    outcomes[index] = future.result()
                except Exception as exc:
                    outcomes[index] = exc

        if cancellation_requested:
            for index, _call in enumerate(batch):
                if index not in outcomes:
                    outcomes[index] = ToolResult.interrupted(
                        "用户已请求取消，工具未开始执行。",
                        error_code="tool_cancelled_before_start",
                        metadata={"underlying_operation_started": False},
                    )
            for index, outcome in list(outcomes.items()):
                if isinstance(outcome, ToolResult):
                    outcomes[index] = _interrupt_completed_result(outcome)
    ordered = [(batch[index], outcomes[index]) for index in range(len(batch))]
    first_error = next(
        (outcome for _, outcome in ordered if isinstance(outcome, BaseException)),
        None,
    )
    if first_error is not None:
        raise ToolBatchExecutionError(ordered, first_error) from first_error
    return [
        (call, outcome)
        for call, outcome in ordered
        if isinstance(outcome, ToolResult)
    ]


def _interrupt_completed_result(result: ToolResult[Any]) -> ToolResult[Any]:
    """取消请求发生后只保留执行事实，不让结果进入业务投影。"""

    if result.status == ToolStatus.INTERRUPTED:
        return result
    return ToolResult.interrupted(
        "用户取消请求已生效，工具结果不再用于本次运行。",
        error_code="tool_cancelled_after_execution",
        metadata={
            "underlying_operation_started": True,
            "underlying_operation_completed": True,
            "original_status": result.status.value,
        },
    )


__all__ = [
    "ToolBatchExecutionError",
    "batch_tool_calls",
    "execute_tool_batch",
]
