"""投影 Agent 每轮规划所需的精简工作状态。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.retrieval import is_compilation_decision_executable
from apps.tool import RetryAdvice, ToolResult, ToolStatus

CRITICAL_SEMANTIC_STATUSES = frozenset(
    {
        "metric_ambiguous",
        "dimension_ambiguous",
        "semantic_ambiguous",
        "time_dimension_not_configured",
        "CLARIFICATION_REQUIRED",
    }
)


def semantic_status(context: dict[str, Any]) -> str | None:
    """从语义包和可信范围中读取统一的语义决策状态。"""

    package = context.get("semantic_package")
    scope = context.get("semantic_scope")
    if isinstance(scope, dict) and scope.get("semantic_enforcement") == "STRICT":
        plan = scope.get("query_plan")
        if isinstance(plan, dict) and plan.get("validation_status"):
            return str(plan["validation_status"])
    if isinstance(package, dict) and package.get("status"):
        return str(package["status"])
    if isinstance(scope, dict) and scope.get("decision_status"):
        return str(scope["decision_status"])
    return None


def has_critical_ambiguity(context: dict[str, Any]) -> bool:
    """判断当前语义结果是否必须先询问用户。"""

    return semantic_status(context) in CRITICAL_SEMANTIC_STATUSES


def has_resolved_semantics(context: dict[str, Any]) -> bool:
    """只有服务端明确标记为可执行的语义范围才允许进入编译。"""

    scope = context.get("semantic_scope")
    if isinstance(scope, dict) and scope.get("semantic_enforcement") == "STRICT":
        plan = scope.get("query_plan")
        report = scope.get("validation_report")
        return bool(
            isinstance(plan, dict)
            and plan.get("validation_status") == "PROVEN"
            and plan.get("fingerprint")
            and isinstance(report, dict)
            and report.get("status") == "PROVEN"
        )
    return bool(
        isinstance(scope, dict)
        and is_compilation_decision_executable(scope.get("decision_status"))
        and scope.get("compile_plan")
    )


def executable_sql(context: dict[str, Any]) -> str | None:
    """返回当前已经编译或校验完成的 SQL。"""

    for key in ("validated_sql", "compiled_sql"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def progress_name(context: dict[str, Any]) -> str:
    """根据可信事实推导当前进度，不维护第二份可漂移的阶段状态。"""

    if context.get("last_execution"):
        return "executed"
    if executable_sql(context):
        return "sql_ready"
    if has_critical_ambiguity(context):
        return "clarification_required"
    if has_resolved_semantics(context):
        return "semantic_resolved"
    if context.get("physical_schema_loaded"):
        return "physical_schema_ready"
    if context.get("semantic_scope"):
        return "semantic_incomplete"
    if context.get("question_understanding"):
        return "understood"
    return "initial"


def recommended_actions(context: dict[str, Any]) -> list[str]:
    """给模型提供基于当前事实的首选动作，不替代模型最终选择。"""

    progress = progress_name(context)
    if progress == "executed":
        return ["finish"]
    if progress == "sql_ready":
        return ["execute_sql"]
    if progress == "clarification_required":
        return ["clarify"]
    if progress == "semantic_resolved":
        return ["compile_semantic_sql"]
    if progress == "physical_schema_ready":
        return ["validate_sql"]
    if progress == "semantic_incomplete":
        return ["get_dataset_schema"]
    if progress == "understood":
        return ["search_semantic_assets"]
    return []


def project_working_state(
    state: AgentRuntimeState,
    mode: str,
    available_tools: list[str],
) -> dict[str, Any]:
    """生成每轮提供给规划模型的精简、稳定状态。"""

    context = state.context.state
    budget = state.budget.snapshot()
    remaining_steps = max(int(budget["max_steps"]) - int(budget["steps"]), 0)
    token_budget = int(budget["token_budget"])
    minimum_completion_steps = _minimum_completion_steps(context)
    observation_history = context.get("tool_observation_history")
    recent_observations = (
        list(observation_history[-5:]) if isinstance(observation_history, list) else []
    )
    return {
        "progress": progress_name(context),
        "semantic": {
            "status": semantic_status(context),
            "resolved": has_resolved_semantics(context),
            "ambiguity_required": has_critical_ambiguity(context),
            "plan_fingerprint": _semantic_plan_fingerprint(context),
            "validation_reason_codes": _semantic_validation_reason_codes(context),
        },
        "artifacts": {
            "semantic_scope_ready": bool(context.get("semantic_scope")),
            "physical_schema_ready": bool(context.get("physical_schema_loaded")),
            "compiled_sql": context.get("compiled_sql"),
            "validated_sql": context.get("validated_sql"),
            "execution_ready": bool(context.get("last_execution")),
        },
        "last_observation": context.get("last_tool_observation"),
        "recent_observations": recent_observations,
        "actions": {
            "recommended": recommended_actions(context),
            "available": available_tools,
        },
        "budget": {
            "mode": mode,
            "remaining_steps": remaining_steps,
            "remaining_tokens": (
                max(token_budget - int(budget["tokens_used"]), 0)
                if token_budget > 0
                else None
            ),
            "minimum_completion_steps": minimum_completion_steps,
            "exploration_allowed": (
                mode == "normal" and remaining_steps > minimum_completion_steps
            ),
        },
    }


def project_tool_observation(
    context: dict[str, Any],
    tool_name: str,
    result: ToolResult[Any],
    *,
    state_changed: bool,
) -> dict[str, Any]:
    """把不同工具结果归一化为模型可以直接纠错的 Observation。"""

    observation: dict[str, Any] = {
        "tool": tool_name,
        "status": result.status.value,
        "progress": _tool_progress(tool_name, result.status, state_changed),
        "state_changed": state_changed,
        "recommended_actions": recommended_actions(context),
    }
    if result.status != ToolStatus.SUCCEEDED:
        observation.update(
            {
                "error_code": result.error_code,
                "error_category": (
                    result.error_category.value
                    if result.error_category is not None
                    else None
                ),
                "details": result.details,
                "retry": {
                    "allowed": result.retry_advice != RetryAdvice.NEVER,
                    "advice": result.retry_advice.value,
                    "recommended_tool": (
                        tool_name
                        if result.retry_advice
                        in {RetryAdvice.SAME_INPUT, RetryAdvice.CORRECT_INPUT}
                        else None
                    ),
                },
            }
        )
    return observation


def _minimum_completion_steps(context: dict[str, Any]) -> int:
    if context.get("last_execution"):
        return 1
    if executable_sql(context):
        return 2
    if has_resolved_semantics(context):
        return 3
    if context.get("physical_schema_loaded"):
        return 3
    return 4


def _tool_progress(
    tool_name: str,
    status: ToolStatus,
    state_changed: bool,
) -> str:
    if status != ToolStatus.SUCCEEDED:
        return "none"
    milestones = {
        "search_semantic_assets": "semantic_retrieved",
        "compile_semantic_sql": "sql_compiled",
        "validate_sql": "sql_validated",
        "execute_sql": "sql_executed",
        "clarify": "clarification_requested",
        "finish": "finished",
    }
    if tool_name in milestones:
        return milestones[tool_name]
    return "information_obtained" if state_changed else "observation_obtained"


def _semantic_plan_fingerprint(context: dict[str, Any]) -> str | None:
    scope = context.get("semantic_scope")
    plan = scope.get("query_plan") if isinstance(scope, dict) else None
    value = plan.get("fingerprint") if isinstance(plan, dict) else None
    return str(value) if value else None


def _semantic_validation_reason_codes(context: dict[str, Any]) -> list[str]:
    scope = context.get("semantic_scope")
    report = scope.get("validation_report") if isinstance(scope, dict) else None
    codes = report.get("reason_codes") if isinstance(report, dict) else []
    return [str(code) for code in codes] if isinstance(codes, list) else []


__all__ = [
    "executable_sql",
    "has_critical_ambiguity",
    "has_resolved_semantics",
    "progress_name",
    "project_tool_observation",
    "project_working_state",
    "recommended_actions",
    "semantic_status",
]
