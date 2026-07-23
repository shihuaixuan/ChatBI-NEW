import pytest

from apps.chatbi.orchestration.graph.capabilities.execution import (
    ExecutionQuery,
    ExecutionResult,
    build_execution_output,
    validate_execution_output,
)


def _query(query_id: str = "query-0") -> ExecutionQuery:
    return ExecutionQuery(
        query_id=query_id,
        sql="select value from t",
        datasource_id=5,
        plan_ref=0,
    )


def _result(
    query_id: str = "query-0",
    status: str = "succeeded",
) -> ExecutionResult:
    return ExecutionResult(
        query_id=query_id,
        status=status,
        row_count=1 if status == "succeeded" else 0,
        fields=["value"] if status == "succeeded" else [],
        sample_rows=[{"value": 1}] if status == "succeeded" else [],
        sampled_row_count=1 if status == "succeeded" else 0,
        result_truncated=False,
        execution_ms=3,
        error_code=None if status == "succeeded" else "sql_execute_error",
        message=None if status == "succeeded" else "查询失败",
    )


def test_build_execution_output_uses_uniform_single_query_shape():
    output = build_execution_output([_query()], [_result()])

    assert output["status"] == "succeeded"
    assert output["queries"][0]["query_id"] == output["results"][0]["query_id"]
    assert output["rows"] == [{"value": 1}]
    assert output["row_count"] == 1
    assert output["fields"] == ["value"]


def test_build_execution_output_aggregates_split_results_without_nested_rows():
    output = build_execution_output(
        [_query("query-0"), _query("query-1")],
        [_result("query-0"), _result("query-1")],
    )

    assert output["status"] == "succeeded"
    assert output["row_count"] == 2
    assert output["execution_ms"] == 6
    assert output["rows"] == []
    assert [item["query_id"] for item in output["results"]] == [
        "query-0",
        "query-1",
    ]


def test_build_execution_output_preserves_success_and_first_failure():
    output = build_execution_output(
        [_query("query-0"), _query("query-1")],
        [_result("query-0"), _result("query-1", status="failed")],
    )

    assert output["status"] == "failed"
    assert [item["status"] for item in output["results"]] == [
        "succeeded",
        "failed",
    ]
    assert output["error_code"] == "sql_execute_error"
    assert output["message"] == "查询失败"


def test_build_execution_output_rejects_misaligned_query_ids():
    with pytest.raises(ValueError, match="EXECUTION_QUERY_RESULT_MISMATCH"):
        build_execution_output([_query("query-0")], [_result("query-1")])


def test_validate_execution_output_marks_empty_success_result():
    output = build_execution_output(
        [_query()],
        [
            ExecutionResult(
                query_id="query-0",
                status="succeeded",
                row_count=0,
                fields=["value"],
                sample_rows=[],
                sampled_row_count=0,
                result_truncated=False,
                execution_ms=3,
            )
        ],
    )

    validation = validate_execution_output(output)

    assert validation["status"] == "empty"
    assert validation["issues"] == [
        {
            "type": "empty_result",
            "query_id": "query-0",
            "message": "查询成功但没有返回数据",
        }
    ]
    assert validation["suggestions"] == ["可以尝试放宽筛选条件或调整时间范围"]


def test_validate_execution_output_passes_non_empty_success_result():
    output = build_execution_output([_query()], [_result()])

    validation = validate_execution_output(output)

    assert validation == {"status": "passed", "issues": [], "suggestions": []}


def test_validate_execution_output_marks_partial_empty_as_suspicious():
    """R3：多查询里 total 子查询为空（part 有值）→ suspicious，而非静默 passed。"""
    part = ExecutionResult(
        query_id="query-0",
        status="succeeded",
        row_count=2,
        fields=["shop_name", "visit_uv"],
        sample_rows=[{"shop_name": "A", "visit_uv": 30}],
        sampled_row_count=1,
        execution_ms=3,
    )
    total_empty = ExecutionResult(
        query_id="query-1",
        status="succeeded",
        row_count=0,
        fields=["visit_uv"],
        sample_rows=[],
        sampled_row_count=0,
        execution_ms=2,
    )
    output = build_execution_output(
        [_query("query-0"), _query("query-1")],
        [part, total_empty],
    )

    validation = validate_execution_output(output)

    assert validation["status"] == "suspicious"
    assert validation["issues"][0]["query_id"] == "query-1"
    assert validation["suggestions"]
