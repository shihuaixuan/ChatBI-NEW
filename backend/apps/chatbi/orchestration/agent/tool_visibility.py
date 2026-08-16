"""根据 ChatBI 当前阶段计算模型可见 Tool。"""

from __future__ import annotations

from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.working_state import (
    executable_sql,
    has_critical_ambiguity,
    has_resolved_semantics,
)

PREPARATION_TOOLS = (
    "parse_time_range",
    "search_semantic_assets",
    "search_terminology",
    "get_sql_examples",
    "get_dataset_schema",
)
STANDARD_TOOLS = frozenset(
    {
        *PREPARATION_TOOLS,
        "compile_semantic_sql",
        "validate_sql",
        "execute_sql",
        "clarify",
        "finish",
    }
)


def visible_tool_names(
    state: AgentRuntimeState,
    mode: str,
    registered: list[str],
) -> list[str]:
    """根据可信进展收缩候选工具，同时保留模型对当前分支的选择权。"""

    available = set(registered)
    context = state.context.state
    if mode == "soft":
        if context.get("last_execution"):
            return _available(("finish",), available)
        if has_critical_ambiguity(context):
            return _available(("clarify",), available)
        if executable_sql(context):
            return _available(("execute_sql",), available)
        if has_resolved_semantics(context):
            return _available(("compile_semantic_sql",), available)
        if context.get("physical_schema_loaded"):
            return _available(("validate_sql",), available)
        return []

    understanding = context.get("question_understanding")
    if not isinstance(understanding, dict):
        return []
    validation = understanding.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "valid":
        # 校验未通过的问题应在进入循环前由 preflight 澄清或拒答收口；
        # 到达这里属于漏网状态，兜底只允许澄清，绝不出现零工具死局。
        return _available(("clarify",), available)
    if context.get("last_execution"):
        return _available(("finish",), available)
    if has_critical_ambiguity(context):
        return _available(("clarify",), available)

    # 严格语义计划未证明时转入语义歧义澄清，而不是零工具等待预算耗尽。
    strict_scope = context.get("semantic_scope")
    if (
        isinstance(strict_scope, dict)
        and strict_scope.get("semantic_enforcement") == "STRICT"
        and not has_resolved_semantics(context)
    ):
        return _available(("clarify",), available)

    if executable_sql(context):
        names = ["execute_sql"]
    elif has_resolved_semantics(context):
        names = ["compile_semantic_sql"]
    elif context.get("physical_schema_loaded"):
        names = ["validate_sql", "get_sql_examples", "get_dataset_schema"]
    elif context.get("semantic_scope"):
        # 语义决策没有收敛时进入物理 SQL 兜底，不重复执行同一语义检索。
        names = ["search_terminology", "get_sql_examples", "get_dataset_schema"]
        if not context.get("time_range"):
            # 计划仍缺可执行时间范围时允许重新解析时间，避免卡死在语义检索阶段。
            names.insert(0, "parse_time_range")
    else:
        names = list(PREPARATION_TOOLS)
    # 显式注册的宿主扩展 Tool 不属于 ChatBI 九工具阶段表，正常模式保持可见。
    names.extend(name for name in registered if name not in STANDARD_TOOLS)
    return _available(tuple(names), available)


def _available(names: tuple[str, ...], available: set[str]) -> list[str]:
    return [name for name in names if name in available]


__all__ = ["visible_tool_names"]
