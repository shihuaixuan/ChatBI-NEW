from apps.chatbi.orchestration.agent.budget import ChatBIBudgetPolicy
from apps.tool import BudgetGuard, RetryAdvice, ToolErrorCategory, ToolResult


def test_step_budget_exhaustion():
    guard = BudgetGuard(max_steps=2)
    assert guard.check_before_step().allowed
    guard.record_llm_turn(None)
    guard.record_llm_turn(None)
    verdict = guard.check_before_step()
    assert not verdict.allowed
    assert verdict.error_class == "budget_exhausted"


def test_token_budget_exhaustion():
    guard = BudgetGuard(token_budget=100)
    guard.record_llm_turn({"total_tokens": 120})
    verdict = guard.check_before_step()
    assert not verdict.allowed
    assert "token" in verdict.reason


def test_repeat_fuse_triggers_on_identical_calls():
    guard = BudgetGuard(repeat_fuse_threshold=3)
    args = {"sql": "select 1"}
    assert guard.check_tool_call("execute_sql", args).allowed
    assert guard.check_tool_call("execute_sql", args).allowed
    verdict = guard.check_tool_call("execute_sql", args)
    assert not verdict.allowed
    assert "重复熔断" in verdict.reason


def test_repeat_fuse_resets_on_different_args():
    guard = BudgetGuard(repeat_fuse_threshold=3)
    assert guard.check_tool_call("t", {"a": 1}).allowed
    assert guard.check_tool_call("t", {"a": 2}).allowed
    assert guard.check_tool_call("t", {"a": 1}).allowed
    assert guard.check_tool_call("t", {"a": 1}).allowed  # 第二次连续
    assert not guard.check_tool_call("t", {"a": 1}).allowed  # 第三次连续熔断


def test_sql_retry_budget():
    guard = ChatBIBudgetPolicy(max_sql_retries=1)
    failed = ToolResult.failed(
        "SQL 错误",
        error_code="sql_error",
        error_category=ToolErrorCategory.DOMAIN,
        retry_advice=RetryAdvice.CORRECT_INPUT,
    )
    guard.record_sql_result("select 1", failed)
    assert guard.check_sql_call("select 2").allowed
    guard.record_sql_result("select 2", failed)
    verdict = guard.check_sql_call("select 3")
    assert not verdict.allowed
    assert verdict.error_class == "sql_failed"


def test_sql_same_input_and_transient_retry_do_not_count_as_correction():
    guard = ChatBIBudgetPolicy(max_sql_retries=1)
    correct_input = ToolResult.failed(
        "SQL 错误",
        error_code="sql_error",
        error_category=ToolErrorCategory.DOMAIN,
        retry_advice=RetryAdvice.CORRECT_INPUT,
    )
    transient = ToolResult.failed(
        "连接超时",
        error_code="timeout",
        error_category=ToolErrorCategory.TRANSIENT,
        retry_advice=RetryAdvice.SAME_INPUT,
    )

    guard.record_sql_result("select 1", correct_input)
    assert guard.check_sql_call("select 1").allowed
    guard.record_sql_result("select 1", transient)

    assert guard.sql_corrections == 0


def test_snapshot_reports_usage():
    guard = BudgetGuard(max_steps=12, token_budget=1000)
    guard.record_llm_turn({"total_tokens": 42})
    snapshot = guard.snapshot()
    assert snapshot["steps"] == 1
    assert snapshot["tokens_used"] == 42
    assert snapshot["max_steps"] == 12
    assert snapshot["planning_mode"] in {"normal", "soft", "exhausted"}


def test_soft_mode_before_hard_exhaustion():
    guard = BudgetGuard(max_steps=5, soft_ratio=0.8)
    for _ in range(4):
        guard.record_system_step()
    assert guard.planning_mode() == "soft"
    assert guard.check_before_step().allowed
