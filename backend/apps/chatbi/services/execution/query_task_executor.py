"""QueryTask 的独立数据库会话执行器。"""

from __future__ import annotations

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
        if request.cancellation.is_cancelled():
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.CANCELLED,
                error_code="query_cancelled",
                message="查询开始前收到取消请求",
            )
        # Session 生命周期严格限制在工作线程内，不能传回主线程复用。
        with self._session_factory() as session:
            service = self._query_service_factory(session)
            call_context = ToolCallContext(
                tool_call_id=f"plan-query:{request.task_id}:{request.attempt}",
                deadline_monotonic=request.deadline_monotonic,
                cancellation=request.cancellation,
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
        if request.cancellation.is_cancelled():
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.CANCELLED,
                error_code="query_cancelled",
                message="查询完成时收到取消请求，结果未合并",
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


__all__ = [
    "QueryTaskExecutionRequest",
    "QueryTaskExecutionResult",
    "QueryTaskExecutionStatus",
    "QueryTaskExecutor",
]
