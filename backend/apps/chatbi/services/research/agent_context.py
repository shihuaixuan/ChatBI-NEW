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

from typing import Any, Literal

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchDirection,
    ResearchEvidence,
    ResearchPremise,
    ToolObservationStatus,
)
from apps.chatbi.services.research.tool_context import ResearchToolContext

# 投影的有界上限：模型只看摘要，完整正文在 ResultStore。
_MAX_EVIDENCE_ITEMS = 12
_MAX_RECENT_FAILURES = 3
_MAX_HYPOTHESES = 20


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
        if requirement.premise_to_verify is not None
        else "没有待验证前提；禁止执行任何未经要求固定的对比查询。"
    )
    return (
        "你是治理范围内的数据研究代理。你的任务是围绕既定目标做多轮"
        "语义查询、检验假设并得出可审计的结论。\n\n"
        "<frozen-boundary>\n"
        f"研究目标（不可修改）：{requirement.goal}\n"
        f"目标指标（不可修改）：{', '.join(requirement.target_metric_refs)}\n"
        f"时间绑定（不可修改）：\n{time_lines}\n"
        + (f"不可变筛选（不可修改）：\n{immutable}\n" if immutable else "")
        + f"数据集：{scope.dataset_ref}；租户范围：{scope.tenant_scope}\n"
        "冻结版本："
        f"schema={requirement.version_snapshot.schema_fingerprint}, "
        f"scope={scope.scope_fingerprint}\n"
        "</frozen-boundary>\n\n"
        "<protocol>\n"
        "1. 每轮至少调用一个规划或完成工具；纯文本回答不构成完成。\n"
        "2. finish_research 必须单独一轮提交，不能和查询工具同批。\n"
        "3. 引用本轮才会产生的证据的工具会被拒绝；依赖工具必须分轮调用。\n"
        "4. 相同内容的查询会去重并返回既有观察，不重复消耗预算。\n"
        "5. 你不能直接执行查询、计算或证据检查；只能通过 assess_research "
        "提交计划节点，Runtime 会从 DAG 执行。\n"
        f"6. {premise_line}\n"
        "7. 你可以调整维度、排序、限制、拆分方式和 Scope 内驱动指标；"
        "不能修改目标指标、时间绑定、不可变筛选或冻结版本。\n"
        "8. Evidence 内容不足时必须用 assess_research 说明明确缺口并提交可编译"
        "的计划增量。首次计划和后续修订使用同一协议；计划批准后由 Runtime "
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
        "output_requirements": list(requirement.output_requirements),
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
        "time_ranges": [role.value for role in item.time_ranges],
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
        }
    ]


__all__ = [
    "build_research_system_context",
    "evaluate_premise_verdict",
    "project_research_working_state",
]
