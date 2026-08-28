"""Research Agent 的受控推理上下文。

本模块是 Harness 与模型之间的信息边界：

- ``build_research_system_context``：独立的系统上下文，声明冻结边界和动作规则；
- ``project_research_react_state``：每轮只投影受控输入、Evidence、Finding、
  Todo、Attempt 和预算；绝不包含物理列、完整 Schema 或其他 Run 的证据。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    ConversationMessage,
    Evidence,
    RemainingBudget,
    ResearchAgentInput,
    ResearchAgentRequirement,
    ResearchState,
    SemanticContext,
)

# 阶段 2 的模型输入上限；完整事实仍保存在运行状态和结果存储中。
_MAX_REACT_EVIDENCE_ITEMS = 12
_MAX_REACT_EVIDENCE_ROWS = 20
_MAX_REACT_FINDINGS = 20
_MAX_REACT_TODOS = 20
_MAX_REACT_ATTEMPTS = 20
_MAX_SEMANTIC_ASSETS_PER_TYPE = 40
_MAX_SEMANTIC_DESCRIPTION_CHARS = 1_000
_MAX_SEMANTIC_CONTEXT_YAML_CHARS = 12_000
_MIN_SEMANTIC_DESCRIPTION_CHARS = 128


def build_research_agent_input(
    user_question: str,
    semantic_context: SemanticContext,
    *,
    conversation_context: Sequence[
        ConversationMessage | Mapping[str, Any]
    ] = (),
    agent_input_ref: str | None = None,
) -> ResearchAgentInput:
    """构造不可修改的 ResearchAgentInput 快照。"""

    messages = tuple(
        item
        if isinstance(item, ConversationMessage)
        else ConversationMessage.model_validate(item)
        for item in conversation_context
    )
    return ResearchAgentInput(
        agent_input_ref=agent_input_ref,
        user_question=user_question,
        conversation_context=messages,
        semantic_context=semantic_context,
    )


def build_research_agent_input_from_requirement(
    requirement: ResearchAgentRequirement,
    *,
    user_question: str,
    semantic_context: SemanticContext,
    conversation_context: Sequence[
        ConversationMessage | Mapping[str, Any]
    ] = (),
    agent_input_ref: str | None = None,
) -> ResearchAgentInput:
    """从路由冻结结果构造输入，并校验语义资产没有越过 Scope。"""

    allowed_metrics = set(requirement.scope.target_metric_refs) | set(
        requirement.scope.driver_metric_refs
    )
    allowed_dimensions = set(requirement.scope.dimension_refs)
    allowed_hierarchy_levels = {
        tuple(item.dimension_refs) for item in requirement.scope.hierarchies
    }
    allowed_refs = allowed_metrics | allowed_dimensions

    def require_allowed(ref: str) -> None:
        if ref not in allowed_refs:
            raise ValueError("RESEARCH_AGENT_INPUT_SEMANTIC_REF_OUT_OF_SCOPE")

    for metric in semantic_context.metrics:
        require_allowed(metric.ref)
        for dimension_ref in metric.dimensions:
            require_allowed(dimension_ref)
    for dimension in semantic_context.dimensions:
        require_allowed(dimension.ref)
    for hierarchy in semantic_context.hierarchies:
        # Scope 中的 hierarchy_id 是治理存储 ID，不能与模型侧正式 ref 拼接；
        # 通过冻结的层级维度顺序绑定语义引用，避免同 ID 不同资产被误放行。
        if hierarchy.levels not in allowed_hierarchy_levels:
            raise ValueError("RESEARCH_AGENT_INPUT_SEMANTIC_REF_OUT_OF_SCOPE")
        for level_ref in hierarchy.levels:
            require_allowed(level_ref)
    for formula in semantic_context.metric_formulas:
        require_allowed(formula.target_metric_ref)
        for source_ref in formula.source_metric_refs:
            require_allowed(source_ref)
    for relation in semantic_context.metric_analysis_relations:
        require_allowed(relation.metric_ref)
        for related_ref in relation.related_metric_refs:
            require_allowed(related_ref)
    for ambiguity in semantic_context.ambiguities:
        for candidate_ref in ambiguity.candidate_refs:
            require_allowed(candidate_ref)

    return build_research_agent_input(
        user_question,
        semantic_context,
        conversation_context=conversation_context,
        agent_input_ref=agent_input_ref,
    )


def serialize_semantic_context_yaml(
    semantic_context: SemanticContext,
    *,
    max_chars: int = _MAX_SEMANTIC_CONTEXT_YAML_CHARS,
) -> str:
    """按固定字段顺序将语义上下文投影为有界 YAML。"""

    if max_chars <= 0:
        raise ValueError("RESEARCH_AGENT_SEMANTIC_CONTEXT_YAML_LIMIT_INVALID")

    # 运行环境未安装 PyYAML 类型存根，运行时仍使用其稳定序列化接口。
    import yaml  # type: ignore[import-untyped]

    asset_limit = _MAX_SEMANTIC_ASSETS_PER_TYPE
    description_limit = _MAX_SEMANTIC_DESCRIPTION_CHARS
    while True:
        bounded = _bound_semantic_context(
            semantic_context,
            asset_limit=asset_limit,
            description_limit=description_limit,
        )
        payload = {"semantic_context": bounded.model_dump(mode="json")}
        serialized = cast(
            str,
            yaml.safe_dump(
                payload,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            ),
        )
        if len(serialized) <= max_chars:
            return serialized
        if description_limit > _MIN_SEMANTIC_DESCRIPTION_CHARS:
            description_limit = max(
                _MIN_SEMANTIC_DESCRIPTION_CHARS,
                description_limit // 2,
            )
        elif asset_limit > 1:
            asset_limit = max(1, asset_limit // 2)
        else:
            raise ValueError("RESEARCH_AGENT_SEMANTIC_CONTEXT_YAML_BUDGET_EXCEEDED")


def project_research_react_state(
    agent_input: ResearchAgentInput,
    state: ResearchState,
    *,
    evidence: Sequence[Evidence] = (),
    remaining_budget: RemainingBudget,
) -> dict[str, Any]:
    """将 ResearchState 投影为模型可读的 ReAct Working State。"""

    if agent_input.agent_input_ref != state.agent_input_ref:
        raise ValueError("RESEARCH_AGENT_INPUT_STATE_REF_MISMATCH")
    input_payload = agent_input.model_dump(mode="json", exclude={"semantic_context"})
    input_payload["semantic_context"] = serialize_semantic_context_yaml(
        agent_input.semantic_context
    )
    evidence_payload = _project_react_evidence(state, evidence)
    return {
        "research_agent_input": input_payload,
        "evidence": evidence_payload,
        "findings": [
            item.model_dump(mode="json")
            for item in state.findings
            if item.status == "confirmed"
        ][-_MAX_REACT_FINDINGS:],
        "todo_items": [
            item.model_dump(mode="json")
            for item in state.todo_items
            if item.status in {"pending", "in_progress"}
        ][-_MAX_REACT_TODOS:],
        "attempted_actions": _project_react_attempts(state.attempted_actions),
        "remaining_budget": remaining_budget.model_dump(mode="json"),
    }


def _bound_semantic_context(
    semantic_context: SemanticContext,
    *,
    asset_limit: int,
    description_limit: int,
) -> SemanticContext:
    """限制资产数量和描述长度，保持原 DTO 的结构与字段顺序。"""

    def clip(value: str) -> str:
        return value[:description_limit]

    return semantic_context.model_copy(
        update={
            "metrics": tuple(
                item.model_copy(update={"description": clip(item.description)})
                for item in semantic_context.metrics[:asset_limit]
            ),
            "dimensions": tuple(
                item.model_copy(update={"description": clip(item.description)})
                for item in semantic_context.dimensions[:asset_limit]
            ),
            "hierarchies": semantic_context.hierarchies[:asset_limit],
            "metric_formulas": semantic_context.metric_formulas[:asset_limit],
            "metric_analysis_relations": semantic_context.metric_analysis_relations[
                :asset_limit
            ],
            "ambiguities": semantic_context.ambiguities[:asset_limit],
        }
    )


def _project_react_evidence(
    state: ResearchState,
    evidence: Sequence[Evidence],
) -> list[dict[str, Any]]:
    """只投影 Evidence 定义、限制和有界结果行，不投影 Tool Call。"""

    by_id = {item.evidence_id: item for item in evidence}
    # Evidence 必须由 ResearchState 的引用集合授权，不能因调用方传入额外数据
    # 就把其他 Run 或尚未挂接到当前状态的证据投影给模型。
    ordered = [by_id[item] for item in state.evidence_refs if item in by_id]
    projected: list[dict[str, Any]] = []
    for item in ordered[-_MAX_REACT_EVIDENCE_ITEMS:]:
        payload = item.model_dump(mode="json")
        data = payload.get("data")
        if isinstance(data, dict):
            data["rows"] = list(data.get("rows") or [])[:_MAX_REACT_EVIDENCE_ROWS]
        projected.append(payload)
    return projected


def _project_react_attempts(
    attempts: Sequence[AttemptSummary],
) -> list[dict[str, Any]]:
    """按动作指纹合并重复失败摘要，并保留最近一次错误。"""

    projected: list[dict[str, Any]] = []
    failure_indexes: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    for attempt in attempts:
        payload = attempt.model_dump(mode="json")
        if attempt.status not in {"failed", "rejected"}:
            projected.append(payload)
            continue
        fingerprint = attempt.action_fingerprint
        failure_counts[fingerprint] = failure_counts.get(fingerprint, 0) + 1
        index = failure_indexes.get(fingerprint)
        if index is None:
            failure_indexes[fingerprint] = len(projected)
            projected.append(payload)
        else:
            # 保留最新错误内容，计数在循环结束后写入，避免旧错误覆盖新错误。
            projected[index] = payload
    for fingerprint, index in failure_indexes.items():
        if failure_counts[fingerprint] > 1:
            projected[index]["repeat_count"] = failure_counts[fingerprint]
    return projected[-_MAX_REACT_ATTEMPTS:]


def build_research_system_context(requirement: ResearchAgentRequirement) -> str:
    """构造 Research Agent 的独立系统上下文。"""

    scope = requirement.scope
    immutable = "\n".join(
        f"- {item.target_ref} {item.operator} {item.value}"
        for item in requirement.immutable_filters
    )
    time_lines = "\n".join(
        f"- {binding.role.value}: {binding.expression}"
        for binding in requirement.time_bindings
    )
    if not time_lines:
        time_lines = "- single: 无显式时间条件"
    hierarchy_lines = "\n".join(
        f"- {hierarchy.hierarchy_id}: {' -> '.join(hierarchy.dimension_refs)}"
        for hierarchy in scope.hierarchies
    )
    operation_lines = "\n".join(
        f"- {item.model_dump(mode='json')}" for item in requirement.operations
    ) or "- 无额外结果操作"
    return (
        "你是治理范围内的数据研究代理。你的任务是围绕既定目标逐步收集"
        "证据并得出可审计的结论。\n\n"
        "<frozen-boundary>\n"
        f"研究目标（不可修改）：{requirement.goal}\n"
        f"目标指标（不可修改）：{', '.join(requirement.target_metric_refs)}\n"
        f"时间绑定（不可修改）：\n{time_lines}\n"
        f"结果操作（不可修改）：\n{operation_lines}\n"
        + (f"不可变筛选（不可修改）：\n{immutable}\n" if immutable else "")
        + f"数据集：{scope.dataset_ref}；租户范围：{scope.tenant_scope}\n"
        "冻结版本："
        f"schema={requirement.version_snapshot.schema_fingerprint}, "
        f"scope={scope.scope_fingerprint}\n"
        "</frozen-boundary>\n\n"
        "<protocol>\n"
        "1. 每轮至少提交一个动作；纯文本回答不构成完成。\n"
        "2. finish_research 必须单独提交，不能与数据动作或澄清动作混合。\n"
        "3. 相同参数的动作会复用已保存结果，不重复执行或消耗预算。\n"
        "4. 你可以调整维度、排序、限制、拆分方式和 Scope 内驱动指标；"
        "不能修改目标指标、时间绑定、结果操作、不可变筛选或冻结版本。"
        "时间 group 操作使用 query_semantic_data.time_grain，不要把时间维度"
        "猜成 dimensions；排序和数量限制必须在 query_semantic_data 的 order、limit"
        "中直接执行，不能只依赖 inspect_evidence 的展示排序。按计算差值排序时，"
        "order 使用指标 ref 并设置 value_role=difference；analysis=contribution"
        "只用于 comparison=contribution，日环比差异的维度定位使用 analysis=breakdown。\n"
        "5. 先处理未完成 Todo；动作完成后根据 ToolResult 和 Evidence 决定下一步。\n"
        "6. 报告只能写证据样本或确定性计算证据中已经存在的数字；没有 "
        "growth_rate/share/contribution 证据时，不得自行换算百分比。\n"
        "7. claim_level=contribution 和因果措辞只能引用 contribution 或 "
        "reconciliation 证据；普通比较证据使用 common_change 或 "
        "correlation_clue，并明确为共同变化或相关线索。\n"
        "8. evidence ID 和资产 ref 只放在结构化引用字段中，不要写进 "
        "summary、finding 或 claim 的正文。\n"
        "</protocol>\n"
        + (f"\n<available-hierarchies>\n{hierarchy_lines}\n</available-hierarchies>\n" if hierarchy_lines else "")
    )


__all__ = [
    "build_research_agent_input",
    "build_research_agent_input_from_requirement",
    "build_research_system_context",
    "project_research_react_state",
    "serialize_semantic_context_yaml",
]
