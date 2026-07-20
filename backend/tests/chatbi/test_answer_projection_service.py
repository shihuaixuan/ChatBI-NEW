import json

from apps.chatbi.models import AnswerProjectionData
from apps.chatbi.services import project_answer_context


def test_answer_projection_applies_plan_result_and_error_whitelists():
    result = project_answer_context(
        AnswerProjectionData(
            raw_question="今日访问量",
            rewritten_question="今日访问量是多少",
            plan={
                "status": "ready",
                "metrics": [{"asset_id": 1}],
                "private_plan": "secret",
            },
            execution={
                "status": "succeeded",
                "row_count": 1,
                "queries": [{"sql": "select secret"}],
                "results": [
                    {
                        "query_id": "query-0",
                        "status": "succeeded",
                        "row_count": 1,
                        "fields": ["value"],
                        "sample_rows": [{"value": 10}],
                        "artifact_ref": {"artifact_id": "artifact-1"},
                        "private_result": "secret",
                    }
                ],
            },
            knowledge={
                "decision": {
                    "status": "accepted",
                    "reason": "口径已确认",
                    "private": "secret",
                },
                "candidate_groups": {"private": True},
            },
            node_failure={
                "node": "generate_sql",
                "error_code": "SQL_GENERATE_FAILED",
                "private": "secret",
            },
            sql_error={"error_code": "SQL_INVALID", "sql": "select secret"},
        )
    ).payload

    serialized = json.dumps(result, ensure_ascii=False)
    assert "select secret" not in serialized
    assert "private_plan" not in serialized
    assert "private_result" not in serialized
    assert "candidate_groups" not in serialized
    assert result["execution"]["results"][0]["sample_rows"] == [
        {"value": 10}
    ]
    assert result["knowledge_decision"] == {
        "status": "accepted",
        "reason": "口径已确认",
    }
    assert result["node_failure"] == {
        "node": "generate_sql",
        "error_code": "SQL_GENERATE_FAILED",
    }
    assert result["sql_error"] == {"error_code": "SQL_INVALID"}


def test_answer_projection_converts_legacy_flat_execution_result():
    result = project_answer_context(
        AnswerProjectionData(
            raw_question="今日访问量",
            rewritten_question="今日访问量",
            execution={
                "status": "succeeded",
                "row_count": 2,
                "fields": ["value"],
                "rows": [{"value": 1}, {"value": 2}],
                "execution_ms": 5,
                "artifact_ref": {"artifact_id": "artifact-1"},
            },
        )
    ).payload

    assert result["execution"]["results"] == [
        {
            "query_id": "query-0",
            "status": "succeeded",
            "row_count": 2,
            "fields": ["value"],
            "sample_rows": [{"value": 1}, {"value": 2}],
            "execution_ms": 5,
            "artifact_ref": {"artifact_id": "artifact-1"},
            "error_code": None,
            "message": None,
        }
    ]


def test_answer_projection_calculates_share_analysis():
    result = project_answer_context(
        AnswerProjectionData(
            raw_question="各店铺访问人数占比",
            rewritten_question="各店铺访问人数占比",
            plan={"strategy": "multi_query"},
            execution={
                "queries": [
                    {"query_id": "query-0", "role": "part"},
                    {"query_id": "query-1", "role": "total"},
                ],
                "results": [
                    {
                        "query_id": "query-0",
                        "sample_rows": [
                            {"shop_name": "A", "visit_uv": 30},
                            {"shop_name": "B", "visit_uv": "70"},
                        ],
                    },
                    {
                        "query_id": "query-1",
                        "sample_rows": [{"visit_uv": 100}],
                    },
                ],
            },
        )
    ).payload

    assert result["execution"]["analysis"] == {
        "kind": "share",
        "metric": "visit_uv",
        "total": 100.0,
        "rows": [
            {
                "dimensions": {"shop_name": "A"},
                "value": 30.0,
                "share": 0.3,
            },
            {
                "dimensions": {"shop_name": "B"},
                "value": 70.0,
                "share": 0.7,
            },
        ],
    }


def test_answer_projection_calculates_comparison_delta_and_zero_baseline():
    common = {
        "queries": [
            {"query_id": "query-0", "role": "current"},
            {"query_id": "query-1", "role": "baseline"},
        ]
    }

    result = project_answer_context(
        AnswerProjectionData(
            raw_question="本月访问人数环比",
            rewritten_question="本月访问人数环比",
            plan={"strategy": "multi_query"},
            execution={
                **common,
                "results": [
                    {"query_id": "query-0", "sample_rows": [{"visit_uv": 120}]},
                    {"query_id": "query-1", "sample_rows": [{"visit_uv": 100}]},
                ],
            },
        )
    ).payload
    zero_baseline = project_answer_context(
        AnswerProjectionData(
            raw_question="本月访问人数环比",
            rewritten_question="本月访问人数环比",
            plan={"strategy": "multi_query"},
            execution={
                **common,
                "results": [
                    {"query_id": "query-0", "sample_rows": [{"visit_uv": 10}]},
                    {"query_id": "query-1", "sample_rows": [{"visit_uv": 0}]},
                ],
            },
        )
    ).payload

    assert result["execution"]["analysis"] == {
        "kind": "comparison",
        "metric": "visit_uv",
        "current": 120.0,
        "baseline": 100.0,
        "delta": 20.0,
        "change_rate": 0.2,
    }
    assert zero_baseline["execution"]["analysis"]["change_rate"] is None


def test_answer_projection_ignores_boolean_and_invalid_numeric_values():
    result = project_answer_context(
        AnswerProjectionData(
            raw_question="各店铺访问人数占比",
            rewritten_question="各店铺访问人数占比",
            plan={"strategy": "multi_query"},
            execution={
                "queries": [
                    {"query_id": "query-0", "role": "part"},
                    {"query_id": "query-1", "role": "total"},
                ],
                "results": [
                    {
                        "query_id": "query-0",
                        "sample_rows": [{"shop_name": "A", "visit_uv": True}],
                    },
                    {
                        "query_id": "query-1",
                        "sample_rows": [{"visit_uv": "invalid"}],
                    },
                ],
            },
        )
    ).payload

    assert "analysis" not in result["execution"]
