"""QueryTask 的独立数据库会话执行器。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from apps.datasource import (
    DatasourceQueryData,
    DatasourceQueryRequest,
    DatasourceQueryService,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.tool.context import (
    CancellationSignal,
    ToolCallContext,
    bind_tool_call_context,
)


class QueryTaskExecutionStatus(StrEnum):
    """查询工作线程可返回的终态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class QueryTaskExecutionRequest:
    task_id: str
    attempt: int
    sql: str
    datasource_id: int
    workspace_id: int
    user_id: int
    selected_tables: tuple[str, ...]
    deadline_monotonic: float | None
    cancellation: CancellationSignal
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class _TaskCancellationSignal:
    """把 Run 取消和当前任务的截止时间合并成一个线程安全信号。"""

    parent: CancellationSignal
    deadline_monotonic: float | None

    def is_cancelled(self) -> bool:
        return self.parent.is_cancelled() or (
            self.deadline_monotonic is not None
            and time.monotonic() >= self.deadline_monotonic
        )


@dataclass(frozen=True, slots=True)
class QueryTaskExecutionResult:
    task_id: str
    attempt: int
    status: QueryTaskExecutionStatus
    data: DatasourceQueryData | None = None
    error_code: str | None = None
    message: str = ""


class QueryTaskExecutor:
    """每次执行创建独立 Session 和 DatasourceQueryService。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        query_service_factory: Callable[[Any], DatasourceQueryService],
    ) -> None:
        self._session_factory = session_factory
        self._query_service_factory = query_service_factory

    def execute(self, request: QueryTaskExecutionRequest) -> QueryTaskExecutionResult:
        cancellation = _TaskCancellationSignal(
            request.cancellation,
            request.deadline_monotonic,
        )
        if cancellation.is_cancelled():
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.CANCELLED,
                error_code=(
                    "query_timeout"
                    if _deadline_exceeded(request.deadline_monotonic)
                    else "query_cancelled"
                ),
                message=(
                    "查询开始前已超过任务截止时间"
                    if _deadline_exceeded(request.deadline_monotonic)
                    else "查询开始前收到取消请求"
                ),
            )
        # Session 生命周期严格限制在工作线程内，不能传回主线程复用。
        with self._session_factory() as session:
            service = self._query_service_factory(session)
            call_context = ToolCallContext(
                tool_call_id=(
                    request.idempotency_key
                    or f"plan-query:{request.task_id}:{request.attempt}"
                ),
                deadline_monotonic=request.deadline_monotonic,
                cancellation=cancellation,
            )
            with bind_tool_call_context(call_context):
                result = service.execute(
                    DatasourceQueryRequest(
                        sql=request.sql,
                        datasource_id=request.datasource_id,
                        subject=DatasourceQuerySubject(
                            user_id=request.user_id,
                            workspace_id=request.workspace_id,
                        ),
                        selected_tables=list(request.selected_tables),
                        deadline_monotonic=request.deadline_monotonic,
                    )
                )
        if cancellation.is_cancelled():
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.CANCELLED,
                error_code=(
                    "query_timeout"
                    if _deadline_exceeded(request.deadline_monotonic)
                    else "query_cancelled"
                ),
                message=(
                    "查询完成时已超过任务截止时间，结果未合并"
                    if _deadline_exceeded(request.deadline_monotonic)
                    else "查询完成时收到取消请求，结果未合并"
                ),
            )
        if result.status is not DatasourceQueryStatus.SUCCEEDED or result.data is None:
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.FAILED,
                error_code=result.error_code or "query_execute_failed",
                message=result.message,
            )
        return QueryTaskExecutionResult(
            task_id=request.task_id,
            attempt=request.attempt,
            status=QueryTaskExecutionStatus.SUCCEEDED,
            data=result.data,
        )


def _deadline_exceeded(deadline_monotonic: float | None) -> bool:
    """判断任务取消是否由截止时间触发。"""

    return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic


__all__ = [
    "QueryTaskExecutionRequest",
    "QueryTaskExecutionResult",
    "QueryTaskExecutionStatus",
    "QueryTaskExecutor",
]
