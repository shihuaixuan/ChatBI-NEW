"""Research Agent 的受控推理上下文（doc38 §9.3.3 / §9.3.4）。

本模块是 Harness 与模型之间的信息边界：

- ``build_research_system_context``：Research Profile 的独立系统上下文，
  声明冻结边界、协议规则和 WHAT 不变量；
- ``project_research_working_state``：每轮 Working State 投影，只包含
  受控摘要——目标、Scope 资产目录、预算、证据摘要与依赖、最近失败
  Observation 和证据需求完成度；绝不包含物理列、完整 Schema、完整大结果
  或其他 Run 的证据；
- ``evaluate_premise_verdict``：校验模型引用的 Evidence 是否足以证明或
  否定前提；无法确定时返回 ``undetermined``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    ConversationMessage,
    Evidence,
    RemainingBudget,
    ResearchAgentInput,
    ResearchAgentRequirement,
    ResearchDirection,
    ResearchEvidence,
    ResearchPremise,
    ResearchState,
    SemanticContext,
    ToolObservationStatus,
)
from apps.chatbi.services.research.tool_context import ResearchToolContext

# 投影的有界上限：模型只看摘要，完整正文在 ResultStore。
_MAX_EVIDENCE_ITEMS = 12
_MAX_RECENT_FAILURES = 3
_MAX_HYPOTHESES = 20

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
    premise_line = (
        "存在待验证前提 Evidence Gap：可以复用既有 Evidence、与原因分析任务"
        "合并，或在必要时新增任务；不得假定必须执行独立前提查询。"
        "用 query_semantic_data 确认前提时，节点 arguments 必须同时满足："
        "metrics 数组包含前提指标、time_ranges 数组覆盖前提的全部时间角色、"
        'comparison="difference"，并携带 purpose；Working State 的 '
        "evidence_gaps[].confirming_query_example 给出了可直接套用的参数模板。"
        if requirement.premise_to_verify is not None
        else "没有待验证前提；禁止执行任何未经要求固定的对比查询。"
    )
    operation_lines = "\n".join(
        f"- {item.model_dump(mode='json')}" for item in requirement.operations
    ) or "- 无额外结果操作"
    return (
        "你是治理范围内的数据研究代理。你的任务是围绕既定目标做多轮"
        "语义查询、检验假设并得出可审计的结论。\n\n"
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
        "1. 每轮至少调用一个规划或完成工具；纯文本回答不构成完成。\n"
        "2. submit_research_plan 和 finish_research 都必须单独一轮提交。\n"
        "3. 一次 submit_research_plan 必须提交当前规划周期的全部节点；节点间"
        "依赖使用 dependency_node_ids 表达。\n"
        "4. 相同内容的查询会去重并返回既有观察，不重复消耗预算。\n"
        "5. 你不能直接执行查询、计算或证据检查；只能通过 submit_research_plan "
        "提交完整计划，Runtime 会从 DAG 执行。\n"
        f"6. {premise_line}\n"
        "7. 你可以调整维度、排序、限制、拆分方式和 Scope 内驱动指标；"
        "不能修改目标指标、时间绑定、结果操作、不可变筛选或冻结版本。"
        "时间 group 操作使用 query_semantic_data.time_grain，不要把时间维度"
        "猜成 dimensions；排序和数量限制必须在 query_semantic_data 的 order、limit"
        "中直接执行，不能只依赖 inspect_evidence 的展示排序。按计算差值排序时，"
        "order 使用指标 ref 并设置 value_role=difference；analysis=contribution"
        "只用于 comparison=contribution，日环比差异的维度定位使用 analysis=breakdown。\n"
        "8. 首次规划没有 Evidence 时直接提交完整计划，不得伪造 explicit_gap；"
        "当前计划完成后仍需继续时，必须说明明确缺口并提交下一份完整计划。"
        "计划批准后由 Runtime "
        "自动执行 READY 节点，不需要也不允许你重放执行工具。\n"
        "9. finish_research 必须携带与当前 Evidence 一致的 SemanticAssessment；"
        "answerable 仍需通过结构覆盖和 Evidence 引用校验。\n"
        "10. 报告只能写证据样本或确定性计算证据中已经存在的数字；没有 "
        "growth_rate/share/contribution 证据时，不得自行换算百分比。\n"
        "11. claim_level=contribution 和因果措辞只能引用 contribution 或 "
        "reconciliation 证据；普通比较证据使用 common_change 或 "
        "correlation_clue，并明确为共同变化或相关线索。\n"
        "12. evidence ID 和资产 ref 只放在结构化引用字段中，不要写进 "
        "summary、finding 或 claim 的正文。\n"
        "</protocol>\n"
        + (f"\n<available-hierarchies>\n{hierarchy_lines}\n</available-hierarchies>\n" if hierarchy_lines else "")
    )


def project_research_working_state(
    ctx: ResearchToolContext,
    *,
    premise_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把当前研究事实投影成有界的 Working State 载荷（§9.3.4）。"""

    requirement = ctx.requirement
    scope = requirement.scope
    budget = ctx.budget
    usage = ctx.budget_usage()
    evidences = ctx.evidences()
    observations = ctx.observations()

    evidence_items = [
        _evidence_summary(item) for item in evidences[-_MAX_EVIDENCE_ITEMS:]
    ]
    failures = [
        {
            "tool_call_id": item.tool_call_id,
            "tool_name": item.tool_name,
            "error_code": item.error_code.value if item.error_code else None,
            "failure_stage": (
                item.failure_stage.value if item.failure_stage else None
            ),
            "message": (item.message or "")[:300],
            "suggested_corrections": list(item.suggested_corrections)[:3],
            "retryable": item.retryable,
            "parameter_retryable": item.parameter_retryable,
            "same_parameter_retryable": item.same_parameter_retryable,
        }
        for item in observations
        if item.status is not ToolObservationStatus.SUCCEEDED
    ][-_MAX_RECENT_FAILURES:]

    hypotheses = _project_hypotheses(ctx, evidences)
    return {
        "iteration": ctx.iteration,
        "goal": requirement.goal,
        "reason": requirement.reason.value,
        "target_metric_refs": list(requirement.target_metric_refs),
        "operations": [
            item.model_dump(mode="json") for item in requirement.operations
        ],
        "evidence_gaps": _planning_evidence_gaps(requirement, premise_result),
        "immutable_filters": [
            {
                "target_ref": item.target_ref,
                "operator": item.operator,
                "value": item.value,
            }
            for item in requirement.immutable_filters
        ],
        "time_bindings": [
            {"role": item.role.value, "expression": item.expression}
            for item in requirement.time_bindings
        ] or [{"role": "single", "expression": None}],
        "asset_catalog": {
            "dataset_ref": scope.dataset_ref,
            "target_metrics": list(scope.target_metric_refs),
            "dimensions": list(scope.dimension_refs),
            "driver_metrics": list(scope.driver_metric_refs),
            "allowed_filters": list(scope.allowed_filter_refs),
            "contribution_metrics": list(scope.contribution_metric_refs),
            "contribution_dimensions": list(scope.contribution_dimension_refs),
            "hierarchies": [
                {
                    "hierarchy_id": item.hierarchy_id,
                    "dimension_refs": list(item.dimension_refs),
                }
                for item in scope.hierarchies
            ],
        },
        "budget": {
            "max_iterations": budget.max_iterations,
            "max_queries": budget.max_queries,
            "max_model_calls": budget.max_model_calls,
            "used_queries": usage.queries,
            "used_model_calls": usage.model_calls,
            "remaining_queries": max(budget.max_queries - usage.queries, 0),
            "remaining_model_calls": max(
                budget.max_model_calls - usage.model_calls, 0
            ),
            "remaining_iterations": max(budget.max_iterations - ctx.iteration, 0),
        },
        "plan_execution_state": ctx.plan_execution_state().model_dump(mode="json"),
        "current_plan_result": ctx.current_plan_result(),
        "evidence_requirements": _requirements_progress(
            requirement,
            evidences,
            premise_result=premise_result,
        ),
        "hypotheses": hypotheses[:_MAX_HYPOTHESES],
        "evidences": evidence_items,
        "recent_failures": failures,
        "finished": ctx.finished,
    }


def evaluate_premise_verdict(
    premise: ResearchPremise,
    evidence: ResearchEvidence | None,
) -> tuple[Literal["supported", "not_supported", "undetermined"], str]:
    """按证据列映射确定前提方向是否成立；无法判定时交给 Agent。"""

    observed = _observed_direction(premise.metric_ref, evidence)
    if premise.expected_direction == ResearchDirection.UNKNOWN:
        return "undetermined", observed
    if observed == "unknown":
        return "undetermined", observed
    if observed == premise.expected_direction.value:
        return "supported", observed
    return "not_supported", observed


def _observed_direction(metric_ref: str, evidence: ResearchEvidence | None) -> str:
    """从证据的逻辑列映射读取 current/previous 值并推导方向。"""

    if evidence is None:
        return "unknown"
    fields: dict[str, str] = {}
    for column in evidence.logical_columns:
        if column.asset_ref == metric_ref and column.result_field:
            if column.value_role in ("current", "previous"):
                fields[column.value_role] = column.result_field
    if evidence.sample_rows and {"current", "previous"} <= fields.keys():
        try:
            if evidence.dimension_refs:
                if (
                    evidence.statistics.truncated
                    or len(evidence.sample_rows) != evidence.statistics.row_count
                ):
                    return "unknown"
                current = sum(
                    float(row[fields["current"]]) for row in evidence.sample_rows
                )
                previous = sum(
                    float(row[fields["previous"]]) for row in evidence.sample_rows
                )
            else:
                row = evidence.sample_rows[0]
                current = float(row[fields["current"]])
                previous = float(row[fields["previous"]])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if current > previous:
                return ResearchDirection.INCREASE.value
            if current < previous:
                return ResearchDirection.DECREASE.value
            return ResearchDirection.STABLE.value

    # 部分语义执行器只为比较结果发布 difference 映射。前提方向仍可由该
    # 受治理列确定，不能因为缺少 current/previous 映射而误报 undetermined。
    difference_field = next(
        (
            column.result_field
            for column in evidence.logical_columns
            if column.asset_ref == metric_ref
            and column.value_role == "difference"
            and column.result_field
        ),
        None,
    )
    if difference_field and evidence.sample_rows:
        try:
            difference = float(evidence.sample_rows[0][difference_field])
        except (KeyError, TypeError, ValueError):
            return "unknown"
        if difference > 0:
            return ResearchDirection.INCREASE.value
        if difference < 0:
            return ResearchDirection.DECREASE.value
        return ResearchDirection.STABLE.value
    return "unknown"


def _evidence_summary(item: ResearchEvidence) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "iteration": item.iteration,
        "purpose": item.purpose[:200],
        "metric_refs": list(item.metric_refs),
        "dimension_refs": list(item.dimension_refs),
        "time_grain": item.time_grain,
        "time_ranges": [role.value for role in item.time_ranges],
        "operations": [
            operation.model_dump(mode="json") for operation in item.operations
        ],
        "row_count": item.statistics.row_count,
        "truncated": item.statistics.truncated,
        "dependencies": [
            {
                "evidence_id": dep.evidence_id,
                "relation": dep.relation,
            }
            for dep in item.dependencies
        ],
        "hypothesis_ids": list(item.hypothesis_ids),
        "limitations": list(item.limitations)[:5],
        "sample_rows": list(item.sample_rows)[:5],
    }


def _requirements_progress(
    requirement: ResearchAgentRequirement,
    evidences: list[ResearchEvidence],
    *,
    premise_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """确定性统计最低结构覆盖；该结果不表示内容足以回答问题。"""

    progress: list[dict[str, Any]] = []
    for req in requirement.evidence_requirements:
        if req.kind == "premise_confirmation":
            covered = req.minimum_count if premise_result is not None else 0
            progress.append(
                {
                    "requirement_id": req.requirement_id,
                    "kind": req.kind,
                    "description": req.description[:200],
                    "minimum_count": req.minimum_count,
                    "covered_count": covered,
                    "minimum_coverage_met": premise_result is not None,
                }
            )
            continue
        required = set(req.required_asset_refs)
        covered = 0
        for item in evidences:
            assets = set(item.metric_refs) | set(item.dimension_refs)
            if not required or required & assets:
                covered += 1
        progress.append(
            {
                "requirement_id": req.requirement_id,
                "kind": req.kind,
                "description": req.description[:200],
                "minimum_count": req.minimum_count,
                "covered_count": covered,
                "minimum_coverage_met": covered >= req.minimum_count,
            }
        )
    return progress


def _project_hypotheses(
    ctx: ResearchToolContext,
    evidences: list[ResearchEvidence],
) -> list[dict[str, Any]]:
    assessments = {
        item.hypothesis_id: item for item in ctx.hypothesis_assessments()
    }
    hypothesis_ids: list[str] = []
    for item in evidences:
        for hypothesis_id in item.hypothesis_ids:
            if hypothesis_id not in hypothesis_ids:
                hypothesis_ids.append(hypothesis_id)
    for hypothesis_id in assessments:
        if hypothesis_id not in hypothesis_ids:
            hypothesis_ids.append(hypothesis_id)
    projected: list[dict[str, Any]] = []
    for hypothesis_id in hypothesis_ids:
        assessment = assessments.get(hypothesis_id)
        projected.append(
            {
                "hypothesis_id": hypothesis_id,
                "assessment": assessment.assessment if assessment else "open",
                "assessment_reason": (
                    assessment.reason[:200] if assessment else None
                ),
            }
        )
    return projected


def _premise_summary(premise: ResearchPremise | None) -> dict[str, Any] | None:
    if premise is None:
        return None
    return {
        "premise_type": premise.premise_type.value,
        "metric_ref": premise.metric_ref,
        "expected_direction": premise.expected_direction.value,
        "time_roles": [role.value for role in premise.time_roles],
        "statement": premise.statement,
    }


def _planning_evidence_gaps(
    requirement: ResearchAgentRequirement,
    premise_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """把待验证前提投影为 Planner 可处理的 Evidence Gap。"""

    premise = requirement.premise_to_verify
    if premise is None:
        return []
    requirement_id = next(
        (
            item.requirement_id
            for item in requirement.evidence_requirements
            if item.kind == "premise_confirmation"
        ),
        "premise-gap",
    )
    # 参数模板使用 query_semantic_data 的正式参数名；投影字段（metric_ref/
    # time_roles）只是 Gap 的描述词汇，不能直接当节点 arguments 使用。
    confirming_query_example = {
        "tool_name": "query_semantic_data",
        "arguments": {
            "metrics": [premise.metric_ref],
            "time_ranges": list(
                dict.fromkeys(item.value for item in premise.time_roles)
            ),
            "comparison": "difference",
            "purpose": (f"确认前提：{premise.statement}" or "确认前提")[:1000],
        },
    }
    return [
        {
            "gap_id": requirement_id,
            "kind": "premise_confirmation",
            "status": (
                str(premise_result.get("status"))
                if premise_result is not None
                else "open"
            ),
            "description": premise.statement or "确认用户陈述的指标事实是否成立",
            "metric_ref": premise.metric_ref,
            "expected_direction": premise.expected_direction.value,
            "time_roles": [item.value for item in premise.time_roles],
            "resolution_evidence_ids": (
                [premise_result["evidence_id"]]
                if premise_result is not None
                and isinstance(premise_result.get("evidence_id"), str)
                else []
            ),
            "confirming_query_example": confirming_query_example,
        }
    ]


__all__ = [
    "build_research_agent_input",
    "build_research_agent_input_from_requirement",
    "build_research_system_context",
    "evaluate_premise_verdict",
    "project_research_react_state",
    "project_research_working_state",
    "serialize_semantic_context_yaml",
]
