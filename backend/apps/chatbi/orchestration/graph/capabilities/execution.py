from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from apps.chatbi.models import ChatBIResultArtifactRef
from apps.datasource import DatasourceDriverResult
from apps.datasource.composition import build_datasource_connection_service
from apps.datasource.services import ConnectionDatasourceQueryExecutor


class SessionSqlExecutionGateway:
    """每次查询使用独立 Session，支持拆分查询线程安全并行。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        execute_tool_factory: Callable[
            [Session], ConnectionDatasourceQueryExecutor
        ] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._execute_tool_factory = execute_tool_factory or (
            lambda session: ConnectionDatasourceQueryExecutor(
                build_datasource_connection_service(session)
            )
        )

    def execute(self, datasource_id: int, sql: str) -> DatasourceDriverResult:
        with self._session_factory() as session:
            return self._execute_tool_factory(session).execute(datasource_id, sql)


class ExecutionQuery(BaseModel):
    """一次可执行 SQL 查询的稳定描述。"""

    query_id: str
    sql: str
    datasource_id: int
    plan_ref: int | None = None
    role: str | None = None
    model_id: int | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """单条查询结果；单查询和拆分查询共用同一结构。"""

    query_id: str
    status: Literal["succeeded", "failed"]
    row_count: int = Field(default=0, ge=0)
    fields: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    sampled_row_count: int = Field(default=0, ge=0)
    result_truncated: bool = False
    artifact_ref: ChatBIResultArtifactRef | None = None
    execution_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    message: str | None = None


class ExecutionValidation(BaseModel):
    """SQL 执行结果校验结论。"""

    status: Literal["passed", "empty", "failed", "suspicious"]
    issues: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


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


def validate_execution_output(execution: dict[str, Any]) -> dict[str, Any]:
    """校验 SQL 执行结果，显式标记空结果，避免把空数据当作正常答案。"""

    results = _execution_results(execution)
    if str(execution.get("status") or "").lower() == "failed":
        error_code = str(execution.get("error_code") or "SQL_EXECUTION_FAILED")
        message = str(execution.get("message") or "SQL 执行失败")
        return ExecutionValidation(
            status="failed",
            issues=[{"type": "execution_failed", "error_code": error_code, "message": message}],
            suggestions=[],
        ).model_dump(mode="json")

    failed_results = [item for item in results if str(item.get("status") or "").lower() == "failed"]
    if failed_results:
        first = failed_results[0]
        return ExecutionValidation(
            status="failed",
            issues=[
                {
                    "type": "execution_failed",
                    "query_id": first.get("query_id"),
                    "error_code": first.get("error_code") or "SQL_EXECUTION_FAILED",
                    "message": first.get("message") or "SQL 执行失败",
                }
            ],
            suggestions=[],
        ).model_dump(mode="json")

    empty_issues = [
        {
            "type": "empty_result",
            "query_id": item.get("query_id") or f"query-{index}",
            "message": "查询成功但没有返回数据",
        }
        for index, item in enumerate(results or [_legacy_result(execution)])
        if _row_count(item) == 0
    ]
    if empty_issues and len(empty_issues) == len(results or [execution]):
        return ExecutionValidation(
            status="empty",
            issues=empty_issues,
            suggestions=["可以尝试放宽筛选条件或调整时间范围"],
        ).model_dump(mode="json")
    if empty_issues:
        return ExecutionValidation(
            status="suspicious",
            issues=empty_issues,
            suggestions=["部分子查询没有返回数据，可以检查筛选条件是否过窄"],
        ).model_dump(mode="json")
    return ExecutionValidation(status="passed").model_dump(mode="json")


def _execution_results(execution: dict[str, Any]) -> list[dict[str, Any]]:
    results = execution.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    if execution:
        return [_legacy_result(execution)]
    return []


def _legacy_result(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_id": "query-0",
        "status": execution.get("status"),
        "row_count": execution.get("row_count", 0),
        "error_code": execution.get("error_code"),
        "message": execution.get("message"),
    }


def _row_count(result: dict[str, Any]) -> int:
    value = result.get("row_count")
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    rows = result.get("sample_rows") or result.get("rows")
    return len(rows) if isinstance(rows, list) else 0
