"""回答模型上下文的字段裁剪与多查询摘要服务。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.answer_projection import (
    AnswerProjectionData,
    AnswerProjectionResult,
)


class AnswerProjectionService:
    """生成不包含 SQL、候选载荷和完整结果的回答上下文。"""

    def project(self, data: AnswerProjectionData) -> AnswerProjectionResult:
        execution = data.execution
        results = execution.get("results")
        if not isinstance(results, list):
            results = []
        if not results and execution:
            # 兼容历史单查询扁平结果，统一转换为多查询结果结构。
            results = [
                {
                    "query_id": "query-0",
                    "status": execution.get("status"),
                    "row_count": execution.get("row_count", 0),
                    "fields": execution.get("fields", []),
                    "sample_rows": execution.get("rows", []),
                    "execution_ms": execution.get("execution_ms", 0),
                    "artifact_ref": execution.get("artifact_ref"),
                    "error_code": execution.get("error_code"),
                    "message": execution.get("message"),
                }
            ]
        projected_results = [
            _project_execution_result(item)
            for item in results
            if isinstance(item, dict)
        ]
        execution_projection = {
            "status": execution.get("status"),
            "validation": _project_validation(execution.get("validation")),
            "row_count": execution.get("row_count", 0),
            "results": projected_results,
            "error_code": execution.get("error_code"),
            "message": execution.get("message"),
        }
        analysis = _project_multi_query_analysis(data.plan, execution, results)
        if analysis:
            execution_projection["analysis"] = analysis

        raw_decision = data.knowledge.get("decision")
        decision = raw_decision if isinstance(raw_decision, dict) else {}
        return AnswerProjectionResult(
            payload={
                "question": {
                    "raw": data.raw_question,
                    "rewritten": data.rewritten_question,
                },
                "plan": _project_plan(data.plan),
                "execution": execution_projection,
                "knowledge_decision": {
                    "status": decision.get("status"),
                    "reason": decision.get("reason"),
                },
                "node_failure": _project_error(data.node_failure),
                "sql_error": _project_error(data.sql_error),
            }
        )


def _project_plan(plan: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "status",
        "strategy",
        "select_mode",
        "metrics",
        "group_bys",
        "filters",
        "having",
        "time",
        "order",
        "limit",
        "issues",
        "infeasible_reason",
    )
    return {key: plan.get(key) for key in allowed if key in plan}


def _project_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "query_id",
        "status",
        "row_count",
        "fields",
        "sample_rows",
        "sampled_row_count",
        "result_truncated",
        "artifact_ref",
        "execution_ms",
        "error_code",
        "message",
    )
    return {key: result.get(key) for key in allowed if key in result}


def _project_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    allowed = ("status", "issues", "suggestions")
    return {key: validation.get(key) for key in allowed if key in validation}


def _project_multi_query_analysis(
    plan: dict[str, Any],
    execution: dict[str, Any],
    results: list[Any],
) -> dict[str, Any]:
    if plan.get("strategy") != "multi_query":
        return {}
    role_results = _results_by_role(execution, results)
    if {"part", "total"}.issubset(role_results):
        return _share_analysis(role_results["part"], role_results["total"])
    if {"current", "baseline"}.issubset(role_results):
        return _comparison_analysis(
            role_results["current"],
            role_results["baseline"],
        )
    return {}


def _results_by_role(
    execution: dict[str, Any],
    results: list[Any],
) -> dict[str, dict[str, Any]]:
    queries = execution.get("queries")
    role_by_query_id = {}
    if isinstance(queries, list):
        role_by_query_id = {
            query.get("query_id"): query.get("role")
            for query in queries
            if isinstance(query, dict)
            and query.get("query_id")
            and query.get("role")
        }
    mapped: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        role = role_by_query_id.get(result.get("query_id"))
        if role:
            mapped[str(role)] = result
    return mapped


def _share_analysis(
    part_result: dict[str, Any],
    total_result: dict[str, Any],
) -> dict[str, Any]:
    total_metric, total = _first_numeric(_first_row(total_result))
    if total_metric is None or total is None or total == 0:
        return {}
    rows = []
    for row in _sample_rows(part_result):
        value = _numeric_value(row.get(total_metric))
        metric: str | None = total_metric
        if value is None:
            metric, value = _first_numeric(row)
        if not metric or value is None:
            continue
        rows.append(
            {
                "dimensions": {
                    key: item for key, item in row.items() if key != metric
                },
                "value": value,
                "share": value / total,
            }
        )
    if not rows:
        return {}
    return {
        "kind": "share",
        "metric": total_metric,
        "total": total,
        "rows": rows,
    }


def _comparison_analysis(
    current_result: dict[str, Any],
    baseline_result: dict[str, Any],
) -> dict[str, Any]:
    current_metric, current = _first_numeric(_first_row(current_result))
    baseline_metric, baseline = _first_numeric(_first_row(baseline_result))
    metric = current_metric or baseline_metric
    if not metric or current is None or baseline is None:
        return {}
    return {
        "kind": "comparison",
        "metric": metric,
        "current": current,
        "baseline": baseline,
        "delta": current - baseline,
        "change_rate": None
        if baseline == 0
        else (current - baseline) / baseline,
    }


def _first_row(result: dict[str, Any]) -> dict[str, Any]:
    rows = _sample_rows(result)
    return rows[0] if rows else {}


def _sample_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("sample_rows")
    return (
        [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, list)
        else []
    )


def _first_numeric(row: dict[str, Any]) -> tuple[str | None, float | None]:
    for key, value in row.items():
        number = _numeric_value(value)
        if number is not None:
            return key, number
    return None, None


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _project_error(error: dict[str, Any]) -> dict[str, Any]:
    allowed = ("node", "capability", "error_code", "message", "retryable")
    return {key: error.get(key) for key in allowed if key in error}


__all__ = ["AnswerProjectionService"]
