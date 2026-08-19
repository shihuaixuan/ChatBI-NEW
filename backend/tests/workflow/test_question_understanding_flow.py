from tests.workflow.run_question_understanding_flow import (
    run_question_understanding_flow,
)


def test_question_understanding_flow_runs_classify_rewrite_intent_without_branching():
    flow = run_question_understanding_flow("今日店铺流量", dataset_id=3, tenant_id=1, user_id=1)

    assert {
        key: value
        for key, value in flow["input"].items()
        if key != "temporal_context"
    } == {
        "question": "今日店铺流量",
        "dataset_id": 3,
        "tenant_id": 1,
        "user_id": 1,
    }
    assert flow["input"]["temporal_context"]["reference_at"] == (
        "2026-07-31T12:00:00+08:00"
    )
    assert [node["node"] for node in flow["nodes"]] == [
        "classify_question",
        "rewrite_question",
        "recognize_intent",
    ]
    assert [node["status"] for node in flow["nodes"]] == ["succeeded", "succeeded", "succeeded"]

    classify, rewrite, intent = flow["nodes"]
    assert classify["input"] == {
        "node_name": "classify_question",
        "request": flow["input"],
        "conversation": {},
        "variables": {},
        "inputs": {},
    }
    assert classify["output"]["category"] == "data"
    assert classify["route"] == {
        "condition": "question.data_or_followup",
        "matched": True,
        "reason_code": "QUESTION_DATA_OR_FOLLOWUP",
        "next_node": "rewrite_question",
    }

    assert rewrite["input"] == {
        "node_name": "rewrite_question",
        "request": flow["input"],
        "conversation": {},
        "variables": {
            "classification": classify["output"],
        },
        "inputs": {},
    }
    assert rewrite["output"] == {
        "original_question": "今日店铺流量",
        "rewrite_question": "今日店铺流量",
        "metric_phrases": ["流量"],
        "dimension_phrases": ["店铺"],
    }
    assert rewrite["route"] == {
        "condition": None,
        "matched": True,
        "reason_code": "REWRITE_READY",
        "next_node": "recognize_intent",
    }

    assert intent["input"] == {
        "node_name": "recognize_intent",
        "request": flow["input"],
        "conversation": {},
        "variables": {
            "classification": classify["output"],
            "rewrite": rewrite["output"],
        },
        "inputs": {},
    }
    assert intent["output"]["intent_type"] == "metric_query"
    assert intent["output"]["ambiguous_slots"] == []
    assert intent["output"]["conflict_slots"] == []
    assert intent["route"] == {
        "condition": "intent.ambiguous",
        "matched": False,
        "reason_code": "INTENT_READY",
        "next_node": "retrieve_knowledge",
    }
    assert flow["final_context_variables"] == {
        "classification": classify["output"],
        "rewrite": rewrite["output"],
        "intent": intent["output"],
    }
