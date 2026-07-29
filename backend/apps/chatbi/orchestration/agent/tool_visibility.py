"""根据 ChatBI 当前阶段计算模型可见 Tool。"""

from __future__ import annotations

from apps.chatbi.orchestration.agent.state import AgentRuntimeState

PREPARATION_TOOLS = (
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
    """工具可见性只表达流程阶段，领域服务仍负责最终安全校验。"""

    available = set(registered)
    context = state.context.state
    if mode == "soft":
        if context.get("last_execution"):
            return _available(("finish",), available)
        if _has_critical_ambiguity(context):
            return _available(("clarify",), available)
        return []

    understanding = context.get("question_understanding")
    if not isinstance(understanding, dict):
        return []
    validation = understanding.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "valid":
        return []
    if context.get("last_execution"):
        return _available(("finish",), available)
    if _has_critical_ambiguity(context):
        return _available(("clarify",), available)

    names = list(PREPARATION_TOOLS)
    if context.get("semantic_scope"):
        names.append("compile_semantic_sql")
    if context.get("allowed_tables"):
        names.extend(("validate_sql", "execute_sql"))
    # 显式注册的宿主扩展 Tool 不属于 ChatBI 九工具阶段表，正常模式保持可见。
    names.extend(name for name in registered if name not in STANDARD_TOOLS)
    return _available(tuple(names), available)


def _has_critical_ambiguity(context: dict) -> bool:
    package = context.get("semantic_package")
    if not isinstance(package, dict):
        return False
    status = str(package.get("status") or "")
    return status in {
        "metric_ambiguous",
        "dimension_ambiguous",
        "semantic_ambiguous",
        "time_dimension_not_configured",
    }


def _available(names: tuple[str, ...], available: set[str]) -> list[str]:
    return [name for name in names if name in available]


__all__ = ["visible_tool_names"]
