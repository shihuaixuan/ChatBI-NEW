from apps.workflow.capabilities.context import ChatBIRunContext, int_or_none


def _request(**overrides):
    payload = {
        "request": {"question": " 最近 7 天销售额 ", "dataset_id": 3, "tenant_id": 10, "user_id": 20},
        "conversation": {"question": "最近 7 天销售额"},
        "variables": {},
        "node_name": "classify_question",
    }
    payload.update(overrides)
    return payload


def test_int_or_none_accepts_int_and_digit_string_only():
    assert int_or_none(3) == 3
    assert int_or_none(" 42 ") == 42
    assert int_or_none(True) is None
    assert int_or_none("abc") is None
    assert int_or_none(None) is None
    assert int_or_none(3.5) is None


def test_context_exposes_request_identity_with_lenient_coercion():
    ctx = ChatBIRunContext(_request())

    assert ctx.raw_question == "最近 7 天销售额"
    assert ctx.dataset_id == 3
    assert ctx.tenant_id == 10
    assert ctx.user_id == 20
    assert ctx.node_name == "classify_question"
    assert ctx.conversation == {"question": "最近 7 天销售额"}


def test_context_tenant_falls_back_to_oid_then_default():
    ctx = ChatBIRunContext({"request": {"oid": "7"}})
    assert ctx.tenant_id == 7
    assert ChatBIRunContext({"request": {}}).tenant_id == 1


def test_context_question_prefers_rewritten_over_raw():
    ctx = ChatBIRunContext(
        _request(variables={"rewrite": {"rewritten_question": " 最近 7 天销售额趋势 "}})
    )
    assert ctx.question == "最近 7 天销售额趋势"

    assert ChatBIRunContext(_request()).question == "最近 7 天销售额"


def test_context_domains_guard_against_non_dict_values():
    ctx = ChatBIRunContext(
        _request(variables={"intent": "corrupted", "knowledge": None, "sql": {"sql": "select 1"}})
    )
    assert ctx.intent == {}
    assert ctx.knowledge == {}
    assert ctx.sql == {"sql": "select 1"}
    assert ctx.node_failure == {}


def test_context_interaction_responses_are_dict_guarded():
    ctx = ChatBIRunContext(
        _request(variables={"intent_response": {"skipped": True}, "rewrite_response": "bad"})
    )
    assert ctx.intent_response == {"skipped": True}
    assert ctx.rewrite_response == {}


def test_run_context_prefers_standard_interaction_response_over_legacy_key():
    ctx = ChatBIRunContext(
        _request(
            variables={
                "interactions": {
                    "ask_metric_selection": {
                        "node_name": "ask_metric_selection",
                        "round": 1,
                        "response": {"metric": 239},
                        "skipped": False,
                    }
                },
                "metric_selection": {"metric": 999},
            }
        )
    )

    assert ctx.interaction("ask_metric_selection")["round"] == 1
    assert ctx.metric_selection == {"metric": 239}


def test_context_tolerates_missing_namespaces():
    ctx = ChatBIRunContext({})
    assert ctx.raw_question == ""
    assert ctx.dataset_id is None
    assert ctx.variables == {}
    assert ctx.question == ""


def test_run_context_prefers_standard_execution_domain():
    ctx = ChatBIRunContext(
        _request(
            variables={
                "execution": {"status": "succeeded", "row_count": 2},
                "sql_execution": {"status": "failed", "row_count": 0},
            }
        )
    )

    assert ctx.execution == {"status": "succeeded", "row_count": 2}
    assert ctx.sql_execution == {"status": "succeeded", "row_count": 2}


def test_run_context_falls_back_to_legacy_sql_execution_domain():
    ctx = ChatBIRunContext(
        _request(
            variables={
                "sql_execution": {"status": "succeeded", "row_count": 1}
            }
        )
    )

    assert ctx.execution == {"status": "succeeded", "row_count": 1}
