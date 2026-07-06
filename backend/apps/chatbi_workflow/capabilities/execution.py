from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlmodel import Session

from apps.agentic_chat.schemas import ToolResult
from apps.agentic_chat.tools.sql_executor import SqlExecuteTool
from apps.workflow_engine.domain.artifact import ArtifactRef


class ResultArtifactStore(Protocol):
    """SQL 完整结果正文存储协议。"""

    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...


class SessionSqlExecutionGateway:
    """每次查询使用独立 Session，支持拆分查询线程安全并行。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        execute_tool_factory: Callable[[Session], SqlExecuteTool] = SqlExecuteTool,
    ) -> None:
        self._session_factory = session_factory
        self._execute_tool_factory = execute_tool_factory

    def run(self, payload: dict[str, Any]) -> ToolResult:
        with self._session_factory() as session:
            return self._execute_tool_factory(session).run(payload)


class ExecutionQuery(BaseModel):
    """一次可执行 SQL 查询的稳定描述。"""

    query_id: str
    sql: str
    datasource_id: int
    plan_ref: int | None = None
    model_id: int | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """单条查询结果；单查询和拆分查询共用同一结构。"""

    query_id: str
    status: Literal["succeeded", "failed"]
    row_count: int = Field(default=0, ge=0)
    fields: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    sampled_row_count: int = Field(default=0, ge=0)
    result_truncated: bool = False
    artifact_ref: ArtifactRef | None = None
    execution_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    message: str | None = None


def build_execution_output(
    queries: list[ExecutionQuery],
    results: list[ExecutionResult],
) -> dict[str, Any]:
    """聚合查询结果并生成迁移期兼容摘要。"""

    query_ids = [item.query_id for item in queries]
    result_ids = [item.query_id for item in results]
    if not queries or query_ids != result_ids:
        raise ValueError("EXECUTION_QUERY_RESULT_MISMATCH")

    first_failure = next(
        (item for item in results if item.status == "failed"),
        None,
    )
    single_result = results[0] if len(results) == 1 else None
    fields = _unique_fields(results)
    artifact_refs = [
        item.artifact_ref.model_dump(mode="json")
        for item in results
        if item.artifact_ref is not None
    ]
    return {
        "status": "failed" if first_failure is not None else "succeeded",
        "queries": [item.model_dump(mode="json") for item in queries],
        "results": [item.model_dump(mode="json") for item in results],
        # 以下顶层字段是旧消费端的迁移期兼容摘要。
        "rows": single_result.sample_rows if single_result is not None else [],
        "row_count": sum(item.row_count for item in results),
        "fields": fields,
        "execution_ms": sum(item.execution_ms for item in results),
        "sampled_row_count": sum(item.sampled_row_count for item in results),
        "result_truncated": any(item.result_truncated for item in results),
        "artifact_ref": (
            single_result.artifact_ref.model_dump(mode="json")
            if single_result is not None and single_result.artifact_ref is not None
            else None
        ),
        "artifact_refs": artifact_refs,
        "error_code": first_failure.error_code if first_failure is not None else None,
        "message": first_failure.message if first_failure is not None else None,
    }


def _unique_fields(results: list[ExecutionResult]) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for result in results:
        for field in result.fields:
            if field in seen:
                continue
            seen.add(field)
            fields.append(field)
    return fields
