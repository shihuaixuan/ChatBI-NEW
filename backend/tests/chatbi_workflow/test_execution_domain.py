import pytest

from apps.chatbi_workflow.capabilities.execution import (
    ExecutionQuery,
    ExecutionResult,
    build_execution_output,
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
