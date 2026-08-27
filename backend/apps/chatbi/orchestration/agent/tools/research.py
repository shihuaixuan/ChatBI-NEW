"""Research 通用工具：Runtime / ResultStore / ComputeEngine 的治理边界。

Research 工具是 Agent 与服务端能力的唯一边界：

- 参数边界：模型只提交"怎么查"；run_id、scope 指纹和版本快照由服务端从
  冻结 Requirement 注入，物理字段和 SQL 被 Pydantic 契约拒绝；
- 结果边界：全量结果只存 ResultStore，模型拿到 Evidence ID + 有界样本 + 统计；
- 错误边界：预期失败统一转成结构化 ``ToolObservation``（稳定错误码 + 阶段 +
  重试建议）；未知程序异常直接向宿主传播，不在工具内吞掉；
- 状态边界：Evidence 归当前 Run 所有；同一 tool_call_id 重放不再写
  ResultStore；同内容请求按确定性指纹去重，不重复消耗预算。

本模块没有 Agent 循环、没有模型调用、没有报告生成，也不注册任何直接 SQL 工具。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.chatbi.errors import ResultArtifactReadError, ResultArtifactWriteError
from apps.chatbi.models.dto.analysis_plan import (
    ComputeDerivation,
    ComputeOperation,
    ComputeTask,
    ResultSetKind,
    ResultSetRef,
    ResultSetSnapshot,
)
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    SemanticOperation,
)
from apps.chatbi.models.dto.research_agent import (
    RESEARCH_COMPLETION_STATUS_BY_REASON,
    ResearchBudgetUsage,
    ResearchClaim,
    ResearchCompletion,
    ResearchCompletionReason,
    ResearchComputeOperation,
    ResearchComputeRequest,
    ResearchDrilldownSpec,
    ResearchEvidence,
    ResearchEvidenceDependency,
    ResearchEvidenceStatistics,
    ResearchEvidenceValueRef,
    ResearchFinishRequest,
    ResearchHypothesisAssessment,
    ResearchInspectEvidenceRequest,
    ResearchLiteralFilter,
    ResearchLogicalColumn,
    ResearchOrder,
    ResearchOrderDirection,
    ResearchPlanNode,
    ResearchQueryComparison,
    ResearchReportFinding,
    ResearchResultRef,
    ResearchSemanticQuery,
    ResearchTimeRole,
    ResearchToolCallRef,
    SemanticAssessment,
    SemanticAssessmentStatus,
    ToolErrorCode,
    ToolFailureStage,
    ToolObservation,
    ToolObservationStatus,
    validate_plan_required_operations,
)
from apps.chatbi.services.computation.errors import (
    ComputeEngineError,
    ComputeOperationError,
)
from apps.chatbi.services.research.completion import evaluate_structural_coverage
from apps.chatbi.services.research.hypothesis_evaluator import (
    HypothesisEvaluationError,
    evaluate_hypothesis_assessments,
)
from apps.chatbi.services.research.premise_gap import (
    diagnose_premise_plan,
    validate_premise_gap_assessment,
)
from apps.chatbi.services.research.report_validator import (
    validate_report_conclusions,
)
from apps.chatbi.services.research.semantic_runtime import semantic_query_plan_id
from apps.chatbi.services.research.tool_context import ResearchToolContext
from apps.conversation import ChatRecordExecutionType
from apps.tool import Tool, ToolConcurrency, ToolExecutionPolicy, ToolSideEffect
from apps.tool.base import json_summary
from apps.tool.context import current_tool_call_context
from apps.tool.registry import ToolRegistry
from apps.tool.result import ToolResult

_MODEL_CONTENT_MAX_CHARS = 4000

_COLUMN_ROLES = Literal[
    "group_key",
    "value",
    "current",
    "previous",
    "difference",
    "growth_rate",
    "share",
    "contribution",
]

RESEARCH_TOOL_NAMES: tuple[str, ...] = (
    "query_semantic_data",
    "inspect_evidence",
    "compute_evidence",
    "submit_research_plan",
    "finish_research",
)


class _ToolArgsModel(BaseModel):
    """模型可见参数的统一约束：禁止多余键，禁止就地修改。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class QuerySemanticDataArgs(_ToolArgsModel):
    """query_semantic_data 参数：不含 run_id / 指纹 / 版本快照。"""

    metrics: tuple[str, ...] = Field(min_length=1)
    dimensions: tuple[str, ...] = ()
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    time_ranges: tuple[ResearchTimeRole, ...] = Field(min_length=1)
    filters: tuple[ResearchLiteralFilter, ...] = ()
    evidence_value_filters: tuple[ResearchEvidenceValueRef, ...] = ()
    comparison: ResearchQueryComparison = ResearchQueryComparison.NONE
    analysis: Literal[
        "compare",
        "breakdown",
        "drilldown",
        "filter_from_result",
        "exploration",
        "contribution",
    ] = "exploration"
    drilldown: ResearchDrilldownSpec | None = None
    order: tuple[ResearchOrder, ...] = ()
    limit: int = Field(default=100, gt=0, le=1000)
    purpose: str = Field(min_length=1, max_length=1000)
    hypothesis_ids: tuple[str, ...] = ()


class InspectEvidenceArgs(_ToolArgsModel):
    """inspect_evidence 参数：只读投影，不携带 run_id。"""

    evidence_id: str = Field(min_length=1, max_length=128)
    logical_column_refs: tuple[str, ...] = ()
    order: tuple[ResearchOrder, ...] = ()
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int | None = Field(default=None, gt=0, le=1000)
    max_rows: int = Field(default=20, gt=0, le=100)
    max_chars: int = Field(default=12_000, gt=0, le=100_000)


class ComputeEvidenceArgs(_ToolArgsModel):
    """compute_evidence 参数：只接受确定性操作和 Evidence 引用。"""

    operation: ResearchComputeOperation
    input_evidence_ids: tuple[str, ...] = Field(min_length=1)
    metric_refs: tuple[str, ...] = ()
    dimension_refs: tuple[str, ...] = ()
    group_by_refs: tuple[str, ...] = ()
    order: tuple[ResearchOrder, ...] = ()
    limit: int | None = Field(default=None, gt=0, le=1000)
    tolerance: float | None = Field(default=None, ge=0)
    purpose: str | None = Field(default=None, min_length=1, max_length=1000)


class FinishResearchArgs(_ToolArgsModel):
    """finish_research 参数：结论必须引用当前 Run 的证据。"""

    reason: ResearchCompletionReason
    semantic_assessment: SemanticAssessment
    summary: str = Field(min_length=1, max_length=4000)
    claims: tuple[ResearchClaim, ...] = ()
    findings: tuple[ResearchReportFinding, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    hypothesis_assessments: tuple[ResearchHypothesisAssessment, ...] = ()
    limitations: tuple[str, ...] = ()
    unanswered_questions: tuple[str, ...] = ()


class SubmitResearchPlanArgs(_ToolArgsModel):
    """submit_research_plan 参数：提交本轮完整计划和可选 Evidence 评估。"""

    plan_nodes: tuple[ResearchPlanNode, ...] = Field(min_length=1)
    assessment: SemanticAssessment | None = None


class _ObservationFailure(Exception):
    """携带结构化观察的预期失败；工具边界把它转成成功信封里的失败观察。"""

    def __init__(self, observation: ToolObservation) -> None:
        self.observation = observation
        super().__init__(observation.message or observation.error_code or "failed")


_ERROR_CATEGORIES = {
    ToolErrorCode.SCOPE_DENIED: "authorization",
    ToolErrorCode.PERMISSION_DENIED: "authorization",
    ToolErrorCode.BUDGET_EXHAUSTED: "business_rule",
    ToolErrorCode.EXECUTION_TIMEOUT: "timeout",
    ToolErrorCode.CANCELLED: "cancellation",
    ToolErrorCode.UNSUPPORTED_CAPABILITY: "domain",
}

def _call_id() -> str:
    call = current_tool_call_context()
    if call is not None and call.tool_call_id:
        return call.tool_call_id
    return "unbound"


def _category(code: ToolErrorCode, stage: ToolFailureStage) -> str:
    if stage is ToolFailureStage.VALIDATION:
        return "validation"
    return _ERROR_CATEGORIES.get(code, "domain")


def _fail(
    ctx: ResearchToolContext,
    tool_name: str,
    *,
    code: ToolErrorCode,
    stage: ToolFailureStage,
    message: str,
    details: dict[str, Any] | None = None,
    corrections: tuple[str, ...] = (),
    retryable: bool = False,
    parameter_retryable: bool = False,
    same_parameter_retryable: bool = False,
    capability_gap: bool = False,
    budget_consumed: ResearchBudgetUsage | None = None,
) -> _ObservationFailure:
    """构造结构化失败观察并抛出；调用方不吞掉、只转换。"""

    observation = ToolObservation(
        run_id=ctx.run_id,
        tool_call_id=_call_id(),
        tool_name=tool_name,
        status=ToolObservationStatus.FAILED,
        failure_stage=stage,
        error_code=code,
        error_category=_category(code, stage),
        retryable=retryable,
        parameter_retryable=parameter_retryable,
        same_parameter_retryable=same_parameter_retryable,
        capability_gap=capability_gap,
        message=message[:2000],
        details=details or {"reason": code.value},
        suggested_corrections=tuple(item[:500] for item in corrections),
        budget_consumed=budget_consumed or ctx.budget_usage(),
    )
    return _ObservationFailure(observation)


def _success(
    ctx: ResearchToolContext,
    tool_name: str,
    *,
    message: str,
    details: dict[str, Any] | None = None,
    result_ids: tuple[str, ...] = (),
    evidence_ids: tuple[str, ...] = (),
    statistics: dict[str, Any] | None = None,
    sample_rows: tuple[dict[str, Any], ...] = (),
    limitations: tuple[str, ...] = (),
    semantic_plan_id: str | None = None,
    budget_consumed: ResearchBudgetUsage | None = None,
) -> ToolObservation:
    return ToolObservation(
        run_id=ctx.run_id,
        tool_call_id=_call_id(),
        tool_name=tool_name,
        status=ToolObservationStatus.SUCCEEDED,
        message=message[:2000],
        details=dict(details or {}),
        result_ids=tuple(dict.fromkeys(result_ids)),
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        statistics=dict(statistics or {}),
        sample_rows=sample_rows,
        limitations=limitations,
        semantic_plan_id=semantic_plan_id,
        budget_consumed=budget_consumed or ctx.budget_usage(),
    )


def _envelope(observation: ToolObservation) -> ToolResult[ToolObservation]:
    return ToolResult.succeeded(
        json_summary(observation.model_dump(mode="json"), _MODEL_CONTENT_MAX_CHARS),
        data=observation,
    )


def _finalize(
    ctx: ResearchToolContext,
    producer: Callable[[], ToolObservation],
) -> ToolResult[ToolObservation]:
    """统一入口：重放 → 执行 → 失败转换 → 记录观察。未知异常不经过这里。"""

    call_id = _call_id()
    stored = ctx.observation(call_id)
    if stored is not None:
        # 同一 Tool Call ID 重放：直接返回既有终态，不重复写 ResultStore。
        return _envelope(stored)
    try:
        observation = producer()
    except _ObservationFailure as exc:
        observation = exc.observation
    ctx.record_observation(observation)
    return _envelope(observation)


def _reject_invalid_args(
    ctx: ResearchToolContext,
    tool_name: str,
    exc: ValidationError,
) -> _ObservationFailure:
    codes = [
        str(error.get("msg", ""))
        for error in exc.errors(include_url=False)
        if error.get("msg")
    ]
    return _fail(
        ctx,
        tool_name,
        code=ToolErrorCode.INVALID_REQUEST,
        stage=ToolFailureStage.VALIDATION,
        parameter_retryable=True,
        message=f"参数不合法：{codes[0] if codes else exc}",
        details={"errors": codes[:20]},
        corrections=tuple(dict.fromkeys(codes))[:5],
    )


def _guard_finished(ctx: ResearchToolContext, tool_name: str) -> None:
    if not ctx.finished:
        return
    completion = ctx.completion
    raise _fail(
        ctx,
        tool_name,
        code=ToolErrorCode.INVALID_REQUEST,
        stage=ToolFailureStage.VALIDATION,
        message="研究已完成，不能再调用研究工具",
        details={
            "completion_status": completion.status if completion else "unknown",
        },
        corrections=("开启新的研究 Run 后再继续",),
    )


def _logical_field(
    evidence: ResearchEvidence,
    ref: str,
    role: str | None = None,
) -> str | None:
    """按冻结映射把逻辑引用解析成结果字段；禁止按位置猜测。"""

    fallback: str | None = None
    for column in evidence.logical_columns:
        if column.asset_ref != ref or not column.result_field:
            continue
        if role is not None and column.value_role == role:
            return column.result_field
        if fallback is None:
            fallback = column.result_field
    return fallback


def _load_snapshot(
    ctx: ResearchToolContext,
    tool_name: str,
    evidence: ResearchEvidence,
) -> ResultSetSnapshot:
    """从 ResultStore 读取证据完整结果；失败统一转成结构化观察。"""

    store = ctx.result_store
    if store is None:
        raise TypeError("RESEARCH_TOOL_RESULT_STORE_REQUIRED")
    payload = ctx.result_set_payload(evidence.result_ref.result_id)
    if payload is None:
        raise _fail(
            ctx,
            tool_name,
            code=ToolErrorCode.RESULT_STORE_FAILED,
            stage=ToolFailureStage.PERSISTENCE,
            same_parameter_retryable=True,
            message=f"结果集 {evidence.result_ref.result_id} 不存在或已清理",
            details={"result_set_id": evidence.result_ref.result_id},
        )
    try:
        ref = ResultSetRef.model_validate(payload)
        execution_id, chat_id, record_id, _ = ctx.execution_identity()
        return store.read(
            ref,
            execution_id=execution_id,
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=chat_id,
            record_id=record_id,
        )
    except (
        ValidationError,
        ValueError,
        ResultArtifactReadError,
        ResultArtifactWriteError,
    ) as exc:
        raise _fail(
            ctx,
            tool_name,
            code=ToolErrorCode.RESULT_STORE_FAILED,
            stage=ToolFailureStage.PERSISTENCE,
            same_parameter_retryable=True,
            message=f"结果集读取失败：{exc}",
            details={"result_set_id": evidence.result_ref.result_id},
        ) from exc


class QuerySemanticDataTool(
    Tool[ResearchToolContext, QuerySemanticDataArgs, ToolObservation]
):
    name = "query_semantic_data"
    title = "执行受治理的语义查询"
    description = (
        "在冻结的语义 Scope 内执行一次声明式语义查询。参数只接受逻辑指标、"
        "维度、时间角色、排序、数量限制和字面过滤；服务端注入 run 与版本边界。成功返回证据 ID、"
        "有界样本和统计；失败返回结构化错误码与重试建议。不接受 SQL 或物理字段。"
    )
    args_model = QuerySemanticDataArgs
    result_model = ToolObservation
    execution = ToolExecutionPolicy(
        side_effect=ToolSideEffect.WRITE,
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        idempotent=True,
    )

    def execute(
        self,
        ctx: ResearchToolContext,
        args: QuerySemanticDataArgs,
    ) -> ToolResult[ToolObservation]:
        return _finalize(ctx, lambda: self._run(ctx, args))

    def _run(
        self,
        ctx: ResearchToolContext,
        args: QuerySemanticDataArgs,
    ) -> ToolObservation:
        _guard_finished(ctx, self.name)
        if ctx.semantic_runtime is None:
            raise TypeError("RESEARCH_TOOL_SEMANTIC_RUNTIME_REQUIRED")
        try:
            query = ResearchSemanticQuery(
                run_id=ctx.run_id,
                scope_fingerprint=ctx.requirement.scope.scope_fingerprint,
                version_snapshot=ctx.requirement.version_snapshot,
                metrics=args.metrics,
                dimensions=args.dimensions,
                time_grain=args.time_grain,
                time_ranges=args.time_ranges,
                filters=args.filters,
                evidence_value_filters=args.evidence_value_filters,
                comparison=args.comparison,
                analysis=args.analysis,
                drilldown=args.drilldown,
                order=args.order,
                limit=args.limit,
                purpose=args.purpose,
                hypothesis_ids=args.hypothesis_ids,
            )
        except ValidationError as exc:
            raise _reject_invalid_args(ctx, self.name, exc) from exc

        plan_id = semantic_query_plan_id(query)
        if ctx.fingerprint_known(plan_id):
            return self._reuse_observation(ctx, plan_id)

        usage = ctx.budget_usage()
        if usage.queries >= ctx.budget.max_queries:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.BUDGET_EXHAUSTED,
                stage=ToolFailureStage.BUDGET,
                message=(
                    f"查询预算已耗尽（{usage.queries}/{ctx.budget.max_queries}），"
                    "请基于已有证据推进或结束研究"
                ),
                details={"max_queries": ctx.budget.max_queries, "used": usage.queries},
                corrections=("总结已有证据并调用 finish_research 结束本轮研究",),
            )

        outcome = ctx.semantic_runtime.execute(
            ctx,
            query,
            requirement=ctx.requirement,
            evidence=ctx.evidences(),
            plan_id=plan_id,
            iteration=ctx.evidence_iteration,
        )
        usage_after = ctx.consume_query()
        if outcome.status != "succeeded":
            # 失败也消耗预算（真实执行成本），但不记录指纹，允许换参重试。
            # internal_code 保留边界门的内部码（如 SEMANTIC_SCOPE_REQUIRED）：
            # 只落 error_code 会把门失配伪装成同一种 PERMISSION_DENIED，
            # 现场排查无从下手（run 1306 演练教训之四）。
            failure_details: dict[str, Any] = {"plan_id": plan_id}
            if outcome.internal_code:
                failure_details["internal_code"] = outcome.internal_code
            raise _fail(
                ctx,
                self.name,
                code=outcome.error_code or ToolErrorCode.EXECUTION_FAILED,
                stage=outcome.failure_stage or ToolFailureStage.EXECUTION,
                retryable=outcome.retryable,
                parameter_retryable=outcome.parameter_retryable,
                same_parameter_retryable=outcome.same_parameter_retryable,
                capability_gap=outcome.capability_gap,
                message=outcome.message or "语义查询失败",
                details=failure_details,
                budget_consumed=usage_after,
            )
        for evidence in outcome.evidence:
            ctx.register_evidence(evidence)
        ctx.remember_fingerprint(
            plan_id,
            evidence_id=f"evidence:{plan_id}",
            result_id=outcome.primary_result_id,
        )
        primary = outcome.evidence[0] if outcome.evidence else None
        return _success(
            ctx,
            self.name,
            message=(
                f"语义查询成功，共 {primary.statistics.row_count if primary else 0} 行"
            ),
            result_ids=tuple(ref.result_id for ref in outcome.result_refs),
            evidence_ids=tuple(item.evidence_id for item in outcome.evidence),
            statistics=({"row_count": primary.statistics.row_count} if primary else {}),
            sample_rows=primary.sample_rows if primary else (),
            semantic_plan_id=outcome.plan_id,
            budget_consumed=usage_after,
        )

    def _reuse_observation(
        self,
        ctx: ResearchToolContext,
        plan_id: str,
    ) -> ToolObservation:
        entry = ctx.fingerprint_entry(plan_id)
        evidence = ctx.evidence(entry.get("evidence_id", ""))
        return _success(
            ctx,
            self.name,
            message="相同查询已执行，复用既有证据，未消耗预算",
            result_ids=((entry["result_id"],) if entry.get("result_id") else ()),
            evidence_ids=((entry["evidence_id"],) if entry.get("evidence_id") else ()),
            statistics=(
                {"row_count": evidence.statistics.row_count} if evidence else {}
            ),
            sample_rows=evidence.sample_rows if evidence else (),
            limitations=("duplicate_query_reuse",),
            semantic_plan_id=plan_id,
        )


class InspectEvidenceTool(
    Tool[ResearchToolContext, InspectEvidenceArgs, ToolObservation]
):
    name = "inspect_evidence"
    title = "按逻辑列读取证据完整结果"
    description = (
        "验证证据所有权后从 ResultStore 读取完整结果，按逻辑列、排序、范围和"
        "采样预算投影。只读、幂等；返回逻辑列与统计，不返回其他 Run 的数据，"
        "也不会把大结果整表带进对话。"
    )
    args_model = InspectEvidenceArgs
    result_model = ToolObservation
    execution = ToolExecutionPolicy(
        side_effect=ToolSideEffect.READ,
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        idempotent=True,
    )

    def execute(
        self,
        ctx: ResearchToolContext,
        args: InspectEvidenceArgs,
    ) -> ToolResult[ToolObservation]:
        return _finalize(ctx, lambda: self._run(ctx, args))

    def _run(
        self,
        ctx: ResearchToolContext,
        args: InspectEvidenceArgs,
    ) -> ToolObservation:
        _guard_finished(ctx, self.name)
        evidence = ctx.evidence(args.evidence_id)
        if evidence is None or evidence.run_id != ctx.run_id:
            known = ctx.known_evidence_ids()[:10]
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"证据 {args.evidence_id} 不属于当前研究 Run",
                details={"evidence_id": args.evidence_id},
                corrections=(
                    ("当前可用证据：" + ", ".join(known),)
                    if known
                    else ("先用 query_semantic_data 产生证据",)
                ),
            )
        columns = self._select_columns(ctx, evidence, args)
        fields = list(
            dict.fromkeys(
                column.result_field for column in columns if column.result_field
            )
        )
        if not fields:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.RESULT_CONTRACT_FAILED,
                stage=ToolFailureStage.PROJECTION,
                message="证据缺少可读取的逻辑列映射",
                details={"evidence_id": evidence.evidence_id},
            )
        snapshot = _load_snapshot(ctx, self.name, evidence)
        ref = snapshot.ref
        rows = [dict(row) for row in snapshot.rows]
        rows = self._sorted_rows(ctx, evidence, rows, args)
        limit = min(args.limit or args.max_rows, args.max_rows)
        page = rows[args.offset : args.offset + limit]
        projected, truncated = _bounded_rows(page, fields, args.max_chars)
        return _success(
            ctx,
            self.name,
            message=(
                f"读取证据 {evidence.evidence_id}："
                f"{len(projected)}/{snapshot.ref.row_count} 行"
            ),
            result_ids=(ref.result_set_id,),
            evidence_ids=(evidence.evidence_id,),
            statistics={
                "total_row_count": snapshot.ref.row_count,
                "returned_row_count": len(projected),
                "truncated": truncated,
            },
            sample_rows=tuple(projected),
            limitations=("result_truncated_by_char_budget",) if truncated else (),
        )

    def _select_columns(
        self,
        ctx: ResearchToolContext,
        evidence: ResearchEvidence,
        args: InspectEvidenceArgs,
    ) -> list[ResearchLogicalColumn]:
        if not args.logical_column_refs:
            return list(evidence.logical_columns)
        index: dict[str, list[ResearchLogicalColumn]] = {}
        for column in evidence.logical_columns:
            index.setdefault(column.asset_ref, []).append(column)
        missing = [ref for ref in args.logical_column_refs if ref not in index]
        if missing:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"逻辑列 {', '.join(missing)} 不在证据中",
                details={"evidence_id": evidence.evidence_id, "missing": missing},
                corrections=("可用逻辑列：" + ", ".join(sorted(index)),),
            )
        return [column for ref in args.logical_column_refs for column in index[ref]]

    def _sorted_rows(
        self,
        ctx: ResearchToolContext,
        evidence: ResearchEvidence,
        rows: list[dict[str, Any]],
        args: InspectEvidenceArgs,
    ) -> list[dict[str, Any]]:
        if not args.order:
            return rows
        keyed: list[tuple[str, bool]] = []
        for item in args.order:
            field = _logical_field(evidence, item.ref, item.value_role)
            if field is None:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"排序引用 {item.ref} 无法映射到证据字段",
                    details={"ref": item.ref, "value_role": item.value_role},
                )
            keyed.append((field, item.direction is ResearchOrderDirection.DESC))
        for field, desc in reversed(keyed):
            rows = _safe_sort(rows, field, desc)
        return rows


class ComputeEvidenceTool(
    Tool[ResearchToolContext, ComputeEvidenceArgs, ToolObservation]
):
    name = "compute_evidence"
    title = "对既有证据执行确定性计算"
    description = (
        "只接受当前 Run 的证据引用和白名单确定性操作：difference、growth_rate、"
        "share、ratio、ranking、topn_other、merge、contribution、reconciliation。"
        "输出保存到 ResultStore 并生成依赖输入证据的新 Evidence；对零分母、"
        "缺列、对账失败返回明确错误码。禁止任意表达式和 SQL。"
    )
    args_model = ComputeEvidenceArgs
    result_model = ToolObservation
    execution = ToolExecutionPolicy(
        side_effect=ToolSideEffect.WRITE,
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        idempotent=True,
    )

    def execute(
        self,
        ctx: ResearchToolContext,
        args: ComputeEvidenceArgs,
    ) -> ToolResult[ToolObservation]:
        return _finalize(ctx, lambda: self._run(ctx, args))

    def _run(
        self,
        ctx: ResearchToolContext,
        args: ComputeEvidenceArgs,
    ) -> ToolObservation:
        _guard_finished(ctx, self.name)
        if ctx.compute_engine is None:
            raise TypeError("RESEARCH_TOOL_COMPUTE_ENGINE_REQUIRED")
        try:
            request = ResearchComputeRequest(
                run_id=ctx.run_id,
                operation=args.operation,
                input_evidence_ids=args.input_evidence_ids,
                metric_refs=args.metric_refs,
                dimension_refs=args.dimension_refs,
                group_by_refs=args.group_by_refs,
                order=args.order,
                limit=args.limit,
                tolerance=args.tolerance,
                purpose=args.purpose,
            )
        except ValidationError as exc:
            raise _reject_invalid_args(ctx, self.name, exc) from exc

        inputs = self._resolve_inputs(ctx, request)
        fingerprint = self._fingerprint(ctx, request, inputs)
        if ctx.fingerprint_known(fingerprint):
            return self._reuse_observation(ctx, fingerprint, args.operation)

        plan = self._build_task(ctx, request, inputs, fingerprint)
        rows, fields, sql = self._execute_plan(ctx, request, inputs, plan)
        ref = self._register_result(ctx, fingerprint, request, fields, rows, sql)
        evidence = self._register_evidence(
            ctx,
            request,
            fingerprint,
            ref,
            inputs,
            fields,
            rows,
        )
        ctx.remember_fingerprint(
            fingerprint,
            evidence_id=evidence.evidence_id,
            result_id=ref.result_set_id,
        )
        return _success(
            ctx,
            self.name,
            message=f"{request.operation.value} 完成，输出 {len(rows)} 行",
            result_ids=(ref.result_set_id,),
            evidence_ids=(evidence.evidence_id,),
            statistics={
                "row_count": len(rows),
                "operation": request.operation.value,
            },
            sample_rows=tuple(rows[:10]),
        )

    # ------------------------------------------------------------------ #
    # 输入解析与指纹
    # ------------------------------------------------------------------ #

    def _resolve_inputs(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
    ) -> list[ResearchEvidence]:
        inputs: list[ResearchEvidence] = []
        for evidence_id in request.input_evidence_ids:
            evidence = ctx.evidence(evidence_id)
            if evidence is None or evidence.run_id != ctx.run_id:
                known = ctx.known_evidence_ids()[:10]
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"输入证据 {evidence_id} 不属于当前研究 Run",
                    details={"evidence_id": evidence_id},
                    corrections=(
                        ("当前可用证据：" + ", ".join(known),)
                        if known
                        else ("先用 query_semantic_data 产生证据",)
                    ),
                )
            inputs.append(evidence)
        return inputs

    def _fingerprint(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
    ) -> str:
        """§8.4.3：指纹覆盖冻结 Schema、规范化参数、输入证据、工具与操作。"""

        canonical = json.dumps(
            {
                "tool": self.name,
                "schema_fingerprint": (
                    ctx.requirement.version_snapshot.schema_fingerprint
                ),
                "run_id": ctx.run_id,
                "request": request.model_dump(mode="json", exclude={"run_id"}),
                "inputs": [item.result_ref.result_id for item in inputs],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"research-compute-{digest}"

    def _reuse_observation(
        self,
        ctx: ResearchToolContext,
        fingerprint: str,
        operation: ResearchComputeOperation,
    ) -> ToolObservation:
        entry = ctx.fingerprint_entry(fingerprint)
        evidence = ctx.evidence(entry.get("evidence_id", ""))
        return _success(
            ctx,
            self.name,
            message="相同计算已存在，复用既有证据",
            result_ids=(entry["result_id"],) if entry.get("result_id") else (),
            evidence_ids=((entry["evidence_id"],) if entry.get("evidence_id") else ()),
            statistics=(
                {
                    "row_count": evidence.statistics.row_count,
                    "operation": operation.value,
                }
                if evidence
                else {"operation": operation.value}
            ),
            sample_rows=evidence.sample_rows if evidence else (),
            limitations=("duplicate_compute_reuse",),
        )

    # ------------------------------------------------------------------ #
    # 任务构建
    # ------------------------------------------------------------------ #

    def _build_task(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
        fingerprint: str,
    ) -> ComputeTask | None:
        """把请求映射成 ComputeEngine 白名单任务；ranking 由工具内确定性完成。"""

        operation = request.operation
        if operation is ResearchComputeOperation.RANKING:
            return None
        if operation in (
            ResearchComputeOperation.DIFFERENCE,
            ResearchComputeOperation.GROWTH_RATE,
        ):
            if len(inputs) != 2:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message="差值/增长率需要恰好 2 个输入证据（当前期在前、对照期在后）",
                    details={"inputs": len(inputs)},
                )
            keys = self._join_keys(ctx, request, inputs[0], inputs[1])
            return ComputeTask(
                id=f"{operation.value}-output",
                operation=ComputeOperation.DIFFERENCE
                if operation is ResearchComputeOperation.DIFFERENCE
                else ComputeOperation.GROWTH_RATE,
                inputs=tuple(request.input_evidence_ids),
                join_on=keys,
                options={},
            )
        if operation is ResearchComputeOperation.SHARE:
            evidence = self._single_input(ctx, request, inputs)
            metric = self._metric_field(ctx, request, evidence)
            options: dict[str, Any] = {"metrics": [metric]}
            dimensions = self._dimension_fields(ctx, request, evidence)
            if dimensions:
                options["dimensions"] = dimensions
            return ComputeTask(
                id="share-output",
                operation=ComputeOperation.SHARE,
                inputs=(request.input_evidence_ids[0],),
                options=options,
            )
        if operation is ResearchComputeOperation.RATIO:
            return self._ratio_task(ctx, request, inputs)
        if operation is ResearchComputeOperation.TOPN_OTHER:
            evidence = self._single_input(ctx, request, inputs)
            dimension = self._single_dimension(ctx, request, evidence)
            metric = self._metric_field(ctx, request, evidence)
            assert request.limit is not None
            return ComputeTask(
                id="topn-output",
                operation=ComputeOperation.TOPN_OTHER,
                inputs=(request.input_evidence_ids[0],),
                options={
                    "dimension": dimension,
                    "metrics": [metric],
                    "top_n": request.limit,
                },
            )
        if operation is ResearchComputeOperation.MERGE:
            if len(inputs) < 2:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message="merge 需要至少 2 个输入证据",
                    details={"inputs": len(inputs)},
                )
            keys = self._join_keys(ctx, request, inputs[0], inputs[1])
            return ComputeTask(
                id="merge-output",
                operation=ComputeOperation.MERGE,
                inputs=tuple(request.input_evidence_ids),
                join_on=keys,
                options={},
            )
        if operation in (
            ResearchComputeOperation.CONTRIBUTION,
            ResearchComputeOperation.RECONCILIATION,
        ):
            return self._contribution_task(ctx, request, inputs)
        raise _fail(
            ctx,
            self.name,
            code=ToolErrorCode.UNSUPPORTED_CAPABILITY,
            stage=ToolFailureStage.PLANNING,
            capability_gap=True,
            message=f"操作 {operation.value} 暂不支持",
            details={"operation": operation.value},
        )

    def _single_input(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
    ) -> ResearchEvidence:
        if len(inputs) != 1:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"{request.operation.value} 需要恰好 1 个输入证据",
                details={"inputs": len(inputs)},
            )
        return inputs[0]

    def _join_keys(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        left: ResearchEvidence,
        right: ResearchEvidence,
    ) -> tuple[str, ...]:
        refs = request.group_by_refs or request.dimension_refs
        keys: list[str] = []
        for ref in refs:
            left_field = _logical_field(left, ref, "group_key")
            right_field = _logical_field(right, ref, "group_key")
            if left_field is None or right_field is None:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"维度 {ref} 无法在两个输入上对齐",
                    details={"ref": ref},
                )
            keys.append(left_field)
        if not keys:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="至少提供一个分组维度用于输入对齐，避免笛卡尔积",
                details={"operation": request.operation.value},
            )
        return tuple(keys)

    def _metric_field(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        evidence: ResearchEvidence,
    ) -> str:
        if len(request.metric_refs) != 1:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"{request.operation.value} 需要恰好 1 个指标",
                details={"metric_refs": len(request.metric_refs)},
            )
        field = _logical_field(evidence, request.metric_refs[0], "value")
        if field is None:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"指标 {request.metric_refs[0]} 无法映射到证据字段",
                details={"evidence_id": evidence.evidence_id},
            )
        return field

    def _dimension_fields(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        evidence: ResearchEvidence,
    ) -> list[str]:
        fields: list[str] = []
        for ref in (*request.dimension_refs, *request.group_by_refs):
            field = _logical_field(evidence, ref, "group_key")
            if field is None:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"维度 {ref} 无法映射到证据字段",
                    details={"evidence_id": evidence.evidence_id, "ref": ref},
                )
            fields.append(field)
        return fields

    def _single_dimension(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        evidence: ResearchEvidence,
    ) -> str:
        fields = self._dimension_fields(ctx, request, evidence)
        if len(fields) != 1:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="topn_other 需要恰好 1 个维度",
                details={"dimensions": len(fields)},
            )
        return fields[0]

    def _ratio_task(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
    ) -> ComputeTask:
        evidence = self._single_input(ctx, request, inputs)
        if len(request.metric_refs) != 2:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="ratio 需要恰好 2 个指标（分子、分母）",
                details={"metric_refs": len(request.metric_refs)},
            )
        numerator = _logical_field(evidence, request.metric_refs[0], "value")
        denominator = _logical_field(evidence, request.metric_refs[1], "value")
        if numerator is None or denominator is None:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="ratio 的指标无法映射到证据字段",
                details={"evidence_id": evidence.evidence_id},
            )
        # 服务端确定性构造受限表达式；零分母返回 NULL，由表达式白名单校验。
        name = f"{numerator}_ratio_{denominator}"[:128]
        expression = (
            f'TRY_CAST("{numerator}" AS DOUBLE) / '
            f'NULLIF(TRY_CAST("{denominator}" AS DOUBLE), 0)'
        )
        return ComputeTask(
            id="ratio-output",
            operation=ComputeOperation.EXPR,
            inputs=(request.input_evidence_ids[0],),
            derive=(ComputeDerivation(name=name, expr=expression),),
            options={},
        )

    def _contribution_task(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
    ) -> ComputeTask:
        if len(inputs) != 2:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="contribution/reconciliation 需要恰好 2 个输入证据（分解在前、总量在后）",
                details={"inputs": len(inputs)},
            )
        if len(request.metric_refs) != 1:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="contribution/reconciliation 需要恰好 1 个指标",
                details={"metric_refs": len(request.metric_refs)},
            )
        breakdown, total = inputs
        metric_ref = request.metric_refs[0]
        difference_column = _logical_field(breakdown, metric_ref, "difference")
        total_difference_column = _logical_field(total, metric_ref, "difference")
        if difference_column is None or difference_column not in self._snapshot_fields(
            breakdown
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=(
                    f"分解证据缺少指标 {metric_ref} 的差值列，"
                    "请先用 query_semantic_data 的 comparison 查询生成"
                ),
                details={"metric_ref": metric_ref},
            )
        if total_difference_column is None:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"总量证据缺少指标 {metric_ref} 的差值列",
                details={"metric_ref": metric_ref},
            )
        dimensions = self._dimension_fields(ctx, request, breakdown)
        options: dict[str, Any] = {
            "difference_column": difference_column,
            "total_difference_column": total_difference_column,
            "output_column": f"{difference_column}_contribution",
        }
        if dimensions:
            options["dimensions"] = dimensions
        if request.tolerance is not None:
            options["reconciliation_tolerance"] = request.tolerance
        return ComputeTask(
            id=f"{request.operation.value}-output",
            operation=ComputeOperation.CONTRIBUTION,
            inputs=tuple(request.input_evidence_ids),
            options=options,
        )

    @staticmethod
    def _snapshot_fields(evidence: ResearchEvidence) -> tuple[str, ...]:
        return tuple(
            column.result_field
            for column in evidence.logical_columns
            if column.result_field
        )

    # ------------------------------------------------------------------ #
    # 执行、登记和派生证据
    # ------------------------------------------------------------------ #

    def _execute_plan(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
        plan: ComputeTask | None,
    ) -> tuple[list[dict[str, Any]], list[str], str | None]:
        if plan is None:
            return self._ranking_rows(ctx, request, inputs[0])
        engine = ctx.compute_engine
        if engine is None:
            raise TypeError("RESEARCH_TOOL_COMPUTE_ENGINE_REQUIRED")
        snapshots = {
            evidence_id: _load_snapshot(ctx, self.name, evidence)
            for evidence_id, evidence in zip(
                request.input_evidence_ids, inputs, strict=True
            )
        }
        try:
            computed = engine.execute(plan, snapshots)
        except ComputeEngineError as exc:
            raise self._engine_failure(ctx, exc) from exc
        return (
            [dict(row) for row in computed.rows],
            [str(field) for field in computed.fields],
            computed.sql,
        )

    def _ranking_rows(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        evidence: ResearchEvidence,
    ) -> tuple[list[dict[str, Any]], list[str], str | None]:
        """ComputeEngine 没有原生排名；工具内做确定性排序 + 截断。"""

        if not request.order:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="ranking 需要至少一个排序引用",
                details={"operation": request.operation.value},
            )
        snapshot = _load_snapshot(ctx, self.name, evidence)
        rows = [dict(row) for row in snapshot.rows]
        for item in reversed(request.order):
            field = _logical_field(evidence, item.ref, item.value_role)
            if field is None:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"排序引用 {item.ref} 无法映射到证据字段",
                    details={"ref": item.ref},
                )
            rows = _safe_sort(
                rows, field, item.direction is ResearchOrderDirection.DESC
            )
        limit = min(request.limit or 100, 1000)
        return rows[:limit], [str(field) for field in snapshot.ref.fields], None

    def _engine_failure(
        self,
        ctx: ResearchToolContext,
        exc: ComputeEngineError,
    ) -> _ObservationFailure:
        if exc.code == "COMPUTE_CONTRIBUTION_RECONCILIATION_FAILED":
            return _fail(
                ctx,
                self.name,
                code=ToolErrorCode.RECONCILIATION_FAILED,
                stage=ToolFailureStage.PROOF,
                parameter_retryable=True,
                message="贡献分解与总差值对账失败，分组数据与总量不一致",
                details={"internal_code": exc.code},
            )
        if exc.code == "COMPUTE_SQL_EXECUTION_FAILED":
            return _fail(
                ctx,
                self.name,
                code=ToolErrorCode.EXECUTION_FAILED,
                stage=ToolFailureStage.EXECUTION,
                same_parameter_retryable=True,
                message="计算引擎执行失败",
                details={"internal_code": exc.code},
            )
        if isinstance(exc, ComputeOperationError) or exc.code.startswith(
            "COMPUTE_EXPR"
        ):
            return _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"计算参数不合法：{exc.code}",
                details={"internal_code": exc.code},
            )
        return _fail(
            ctx,
            self.name,
            code=ToolErrorCode.EXECUTION_FAILED,
            stage=ToolFailureStage.EXECUTION,
            same_parameter_retryable=True,
            message=f"计算失败：{exc.code}",
            details={"internal_code": exc.code},
        )

    def _register_result(
        self,
        ctx: ResearchToolContext,
        fingerprint: str,
        request: ResearchComputeRequest,
        fields: list[str],
        rows: list[dict[str, Any]],
        sql: str | None,
    ) -> ResultSetRef:
        if ctx.result_store is None:
            raise TypeError("RESEARCH_TOOL_RESULT_STORE_REQUIRED")
        execution_id, chat_id, record_id, _ = ctx.execution_identity()
        try:
            ref = ctx.result_store.register(
                execution_id=execution_id,
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=chat_id,
                record_id=record_id,
                plan_id=fingerprint,
                node_id=f"{request.operation.value}-output",
                kind=ResultSetKind.COMPUTE,
                fields=fields,
                rows=rows,
                row_count=len(rows),
                source_sql=sql,
                idempotency_key=f"research-compute:{ctx.run_id}:{fingerprint}",
            )
        except (ResultArtifactWriteError, ValueError) as exc:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.RESULT_STORE_FAILED,
                stage=ToolFailureStage.PERSISTENCE,
                same_parameter_retryable=True,
                message=f"计算结果保存失败：{exc}",
                details={"plan_id": fingerprint},
            ) from exc
        ctx.merge_result_ref(ref)
        return ref

    def _register_evidence(
        self,
        ctx: ResearchToolContext,
        request: ResearchComputeRequest,
        fingerprint: str,
        ref: ResultSetRef,
        inputs: list[ResearchEvidence],
        fields: list[str],
        rows: list[dict[str, Any]],
    ) -> ResearchEvidence:
        iteration = ctx.advance_evidence_iteration(
            minimum=max(item.iteration for item in inputs) + 1
        )
        logical_columns = self._derived_columns(request, inputs, fields)
        time_ranges: list[ResearchTimeRole] = []
        for item in inputs:
            for role in item.time_ranges:
                if role not in time_ranges:
                    time_ranges.append(role)
        evidence = ResearchEvidence(
            run_id=ctx.run_id,
            evidence_id=f"evidence:{fingerprint}",
            source_tool_call=ResearchToolCallRef(
                run_id=ctx.run_id,
                tool_call_id=_call_id(),
            ),
            result_ref=ResearchResultRef(
                run_id=ctx.run_id,
                result_id=ref.result_set_id,
            ),
            iteration=iteration,
            purpose=(
                request.purpose
                or f"{request.operation.value} 计算基于证据 "
                f"{', '.join(request.input_evidence_ids)}"
            ),
            metric_refs=request.metric_refs,
            dimension_refs=tuple(
                dict.fromkeys((*request.dimension_refs, *request.group_by_refs))
            ),
            time_ranges=tuple(time_ranges),
            operations=_compute_operations(request),
            logical_columns=logical_columns,
            statistics=ResearchEvidenceStatistics(row_count=len(rows)),
            sample_rows=tuple(dict(row) for row in rows[:10]),
            dependencies=tuple(
                ResearchEvidenceDependency(
                    evidence_id=item.evidence_id,
                    run_id=ctx.run_id,
                    source_iteration=item.iteration,
                    relation=request.operation.value,
                )
                for item in inputs
            ),
            version_snapshot=ctx.requirement.version_snapshot,
        )
        ctx.register_evidence(evidence)
        return evidence

    def _derived_columns(
        self,
        request: ResearchComputeRequest,
        inputs: list[ResearchEvidence],
        fields: list[str],
    ) -> tuple[ResearchLogicalColumn, ...]:
        """把输出字段映射回逻辑列：继承输入映射，派生列按规则合成。"""

        inherited: dict[str, ResearchLogicalColumn] = {}
        for evidence in inputs:
            for column in evidence.logical_columns:
                if column.result_field and column.result_field not in inherited:
                    inherited[column.result_field] = column
        primary_metric = next(iter(request.metric_refs), None)
        primary_dimension = next(
            (ref for ref in (*request.dimension_refs, *request.group_by_refs)),
            None,
        )
        columns: list[ResearchLogicalColumn] = []
        seen: set[tuple[str, str, str]] = set()
        for field in fields:
            resolved = inherited.get(field)
            if resolved is None:
                resolved = self._synthetic_column(
                    field,
                    primary_metric,
                    primary_dimension,
                )
            if resolved is None:
                continue
            key = (resolved.asset_ref, resolved.value_role, field)
            if key in seen:
                continue
            seen.add(key)
            columns.append(
                ResearchLogicalColumn(
                    asset_ref=resolved.asset_ref,
                    value_role=resolved.value_role,
                    result_field=field,
                )
            )
        return tuple(columns)

    @staticmethod
    def _synthetic_column(
        field: str,
        metric_ref: str | None,
        dimension_ref: str | None,
    ) -> ResearchLogicalColumn | None:
        """为计算派生列合成逻辑映射；无法归属的字段跳过。"""

        def build(
            asset_ref: str | None,
            role: _COLUMN_ROLES,
        ) -> ResearchLogicalColumn | None:
            if asset_ref is None:
                return None
            return ResearchLogicalColumn(
                asset_ref=asset_ref, value_role=role, result_field=field
            )

        if field == "dimension_value":
            return build(dimension_ref, "group_key")
        if field == "metric_value":
            return build(metric_ref, "value")
        if field.endswith("_share"):
            return build(metric_ref, "share")
        if field.endswith("_growth_rate"):
            return build(metric_ref, "growth_rate")
        if field.endswith("_difference"):
            return build(metric_ref, "difference")
        if field.endswith("_current"):
            return build(metric_ref, "current")
        if field.endswith("_previous"):
            return build(metric_ref, "previous")
        if field.endswith("_contribution") or field == "contribution":
            return build(metric_ref, "contribution")
        if field == "total_difference" or field == "reconciliation_difference":
            return build(metric_ref, "difference")
        return build(metric_ref, "value")


def _compute_operations(
    request: ResearchComputeRequest,
) -> tuple[SemanticOperation, ...]:
    """从确定性计算请求推导 Evidence 已完成的用户操作。"""

    if request.operation.value not in {item.value for item in CalculationOperation}:
        return ()
    return (
        SemanticOperation(
            type="calculate",
            calculation=CalculationOperation(request.operation.value),
        ),
    )


def validate_research_plan_addition(
    ctx: ResearchToolContext,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    dependency_node_ids: tuple[str, ...] = (),
) -> ResearchSemanticQuery | None:
    """使用实际工具契约校验计划节点，依赖输出在执行时解析为 Evidence。

    query 节点返回编译后的 ``ResearchSemanticQuery``，供调用方做计划级
    的结果操作覆盖检查；其余节点返回 ``None``。
    """

    if tool_name == "query_semantic_data":
        query_args = QuerySemanticDataArgs.model_validate(arguments)
        query = ResearchSemanticQuery(
            run_id=ctx.run_id,
            scope_fingerprint=ctx.requirement.scope.scope_fingerprint,
            version_snapshot=ctx.requirement.version_snapshot,
            **query_args.model_dump(),
        )
        ctx.requirement.validate_query(query, ctx.evidences())
        return query
    elif tool_name == "inspect_evidence":
        normalized = dict(arguments)
        if dependency_node_ids and normalized.get("evidence_id"):
            raise ValueError("RESEARCH_PLAN_DEPENDENCY_EVIDENCE_ARGUMENT_CONFLICT")
        if dependency_node_ids:
            normalized["evidence_id"] = "pending"
        inspect_args = InspectEvidenceArgs.model_validate(normalized)
        ResearchInspectEvidenceRequest(
            run_id=ctx.run_id,
            **inspect_args.model_dump(),
        )
        evidence = ctx.evidence(inspect_args.evidence_id)
        if evidence is None and inspect_args.evidence_id != "pending":
            raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        available_refs = (
            {item.asset_ref for item in evidence.logical_columns}
            if evidence is not None
            else set(ctx.requirement.scope.dimension_refs)
            | set(ctx.requirement.scope.target_metric_refs)
            | set(ctx.requirement.scope.driver_metric_refs)
        )
        if not set(inspect_args.logical_column_refs) <= available_refs:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_COLUMN_NOT_FOUND")
        return None
    else:
        normalized = dict(arguments)
        if dependency_node_ids and normalized.get("input_evidence_ids"):
            raise ValueError("RESEARCH_PLAN_DEPENDENCY_EVIDENCE_ARGUMENT_CONFLICT")
        if dependency_node_ids:
            normalized["input_evidence_ids"] = ("pending",)
        compute_args = ComputeEvidenceArgs.model_validate(normalized)
        request = ResearchComputeRequest(
            run_id=ctx.run_id,
            **compute_args.model_dump(),
        )
        if any(
            evidence_id != "pending" and ctx.evidence(evidence_id) is None
            for evidence_id in request.input_evidence_ids
        ):
            raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        allowed_refs = (
            set(ctx.requirement.scope.target_metric_refs)
            | set(ctx.requirement.scope.driver_metric_refs)
            | set(ctx.requirement.scope.dimension_refs)
        )
        if not (
            set(request.metric_refs)
            | set(request.dimension_refs)
            | set(request.group_by_refs)
        ) <= allowed_refs:
            raise ValueError("RESEARCH_AGENT_COMPUTE_REF_OUT_OF_SCOPE")
        return None


class SubmitResearchPlanTool(
    Tool[ResearchToolContext, SubmitResearchPlanArgs, ToolObservation]
):
    name = "submit_research_plan"
    title = "提交当前规划周期的完整计划"
    description = (
        "首次规划直接提交解决当前问题所需的完整数据步骤；后续重新规划需要同时提交"
        "当前 Evidence 的明确缺口评估。计划由 query_semantic_data、inspect_evidence "
        "或 compute_evidence 节点组成，服务端整体校验后一次性写入新的 DAG revision。"
        "节点 arguments 必须使用工具正式参数名：query_semantic_data 使用 "
        "metrics、dimensions、time_grain、time_ranges、filters、comparison、"
        "analysis、drilldown、order、limit、purpose、hypothesis_ids。order 每项使用 "
        "ref、direction、value_role；按日环比差值排序使用指标 ref 加 "
        "value_role=\"difference\"，不能把 difference 写进 ref。"
        "analysis=\"contribution\" 仅用于 comparison=\"contribution\"；"
        "按维度定位日环比来源使用 comparison=\"difference\"、"
        "analysis=\"breakdown\"。禁止使用 metric、group_by、order_by、time_filter、"
        "dataset_ref 等旧名。"
    )
    args_model = SubmitResearchPlanArgs
    result_model = ToolObservation
    execution = ToolExecutionPolicy(
        side_effect=ToolSideEffect.WRITE,
        concurrency=ToolConcurrency.SERIAL,
        idempotent=True,
    )

    def execute(
        self,
        ctx: ResearchToolContext,
        args: SubmitResearchPlanArgs,
    ) -> ToolResult[ToolObservation]:
        return _finalize(ctx, lambda: self._run(ctx, args))

    def _run(
        self,
        ctx: ResearchToolContext,
        args: SubmitResearchPlanArgs,
    ) -> ToolObservation:
        _guard_finished(ctx, self.name)
        assessment = args.assessment
        if ctx.plan_execution_state().nodes and assessment is None:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="重新规划必须提交针对当前 Evidence 的内容充分性评估。",
            )
        if (
            assessment is not None
            and assessment.status is not SemanticAssessmentStatus.EXPLICIT_GAP
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="提交新计划时，Evidence 评估状态必须是 explicit_gap。",
            )
        known_gap_ids = (
            {item.gap_id for item in assessment.unresolved_gaps}
            if assessment is not None
            else set()
        )
        # 服务端冻结的 Evidence Gap（如前提确认）允许直接引用；只有引用
        # 不存在的 gap_id 才视为伪造。Working State 会向模型投影这些 id。
        frozen_gap_ids = {
            item.requirement_id for item in ctx.requirement.evidence_requirements
        }
        known_gap_ids |= frozen_gap_ids
        if assessment is None and any(
            item.gap_id is not None and item.gap_id not in frozen_gap_ids
            for item in args.plan_nodes
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=(
                    "首次规划不得伪造 Gap：计划节点的 gap_id 必须留空，"
                    "或引用冻结 Requirement 中已存在的 Evidence Gap "
                    f"（当前可用：{sorted(frozen_gap_ids)}）。"
                ),
            )
        if any(
            item.gap_id is not None and item.gap_id not in known_gap_ids
            for item in args.plan_nodes
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="计划节点引用了当前评估中不存在的 Gap。",
            )
        known = set(ctx.known_evidence_ids())
        finding_refs = {
            evidence_id
            for finding in (assessment.supported_findings if assessment else ())
            for evidence_id in finding.evidence_ids
        }
        missing = sorted(finding_refs - known)
        if missing:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="语义评估引用了不属于当前 Run 的 Evidence。",
                details={"missing_evidence_ids": missing},
            )

        # 先逐节点编译：参数形状错误在这里获得最精确的定位（缺哪个键、
        # 多了哪个键）；之后的语义校验只需面对形状合法的计划。
        compiled_queries: list[ResearchSemanticQuery] = []
        for plan_node in args.plan_nodes:
            try:
                compiled = validate_research_plan_addition(
                    ctx,
                    plan_node.tool_name,
                    plan_node.arguments,
                    dependency_node_ids=plan_node.dependency_node_ids,
                )
            except (ValidationError, ValueError) as exc:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=True,
                    message=f"计划节点无法编译或超出 Scope：{exc}",
                    details={"tool_name": plan_node.tool_name},
                ) from exc
            if compiled is not None:
                compiled_queries.append(compiled)

        try:
            validate_plan_required_operations(ctx.requirement, compiled_queries)
        except ValueError as exc:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"完整计划未覆盖用户要求的结果操作：{exc}",
                suggested_corrections=(
                    "把缺失的操作并入现有查询节点（补充 dimensions/order/limit），"
                    "或新增能够表达该操作的节点。",
                ),
            ) from exc

        try:
            premise_result = validate_premise_gap_assessment(
                ctx.requirement,
                ctx.evidences(),
                assessment,
                current_result=ctx.premise_result,
                plan_nodes=args.plan_nodes,
            )
        except ValueError as exc:
            details: dict[str, Any] | None = None
            corrections: tuple[str, ...] = ()
            if str(exc) == "RESEARCH_AGENT_PREMISE_GAP_PLAN_INVALID":
                details = {
                    "plan_node_diagnostics": list(
                        diagnose_premise_plan(ctx.requirement, args.plan_nodes)
                    ),
                }
                corrections = (
                    "按 plan_node_diagnostics 逐节点修正 arguments；参数模板见 "
                    "Working State 的 evidence_gaps[].confirming_query_example。",
                )
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"Evidence Gap 评估校验失败：{exc}",
                details=details,
                suggested_corrections=corrections,
            ) from exc
        try:
            ctx.submit_plan(args.plan_nodes)
            if premise_result is not None:
                ctx.set_premise_result(premise_result)
            if assessment is not None:
                ctx.set_semantic_assessment(assessment)
        except (ValidationError, ValueError) as exc:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"完整计划无法写入统一 DAG：{exc}",
            ) from exc
        return _success(
            ctx,
            self.name,
            message="完整计划已接受并写入统一 DAG",
            evidence_ids=tuple(sorted(finding_refs)),
            statistics={
                "unresolved_gaps": (
                    len(assessment.unresolved_gaps) if assessment else 0
                ),
                "plan_nodes": len(args.plan_nodes),
            },
            limitations=assessment.limitations if assessment else (),
        )

class FinishResearchTool(
    Tool[ResearchToolContext, FinishResearchArgs, ToolObservation]
):
    name = "finish_research"
    title = "提交研究结论并结束当前 Run"
    description = (
        "提交结束请求：finish 原因、结论、证据引用、假设评估、局限和未解决问题。"
        "所有数据结论必须引用当前 Run 的既有证据；通过结构校验和引用存在性校验后"
        "写入终态 completion。findings 必须与 semantic_assessment.supported_findings"
        "完全一致。必须是当前轮最后的收口动作。"
    )
    args_model = FinishResearchArgs
    result_model = ToolObservation
    execution = ToolExecutionPolicy(
        side_effect=ToolSideEffect.WRITE,
        concurrency=ToolConcurrency.SERIAL,
        idempotent=True,
    )

    def execute(
        self,
        ctx: ResearchToolContext,
        args: FinishResearchArgs,
    ) -> ToolResult[ToolObservation]:
        return _finalize(ctx, lambda: self._run(ctx, args))

    def _run(
        self,
        ctx: ResearchToolContext,
        args: FinishResearchArgs,
    ) -> ToolObservation:
        try:
            request = ResearchFinishRequest(
                run_id=ctx.run_id,
                reason=args.reason,
                semantic_assessment=args.semantic_assessment,
                summary=args.summary,
                claims=args.claims,
                findings=args.findings,
                evidence_ids=args.evidence_ids,
                hypothesis_assessments=args.hypothesis_assessments,
                limitations=args.limitations,
                unanswered_questions=args.unanswered_questions,
            )
        except ValidationError as exc:
            raise _reject_invalid_args(ctx, self.name, exc) from exc
        current_assessment = ctx.semantic_assessment()
        if (
            current_assessment is not None
            and current_assessment != request.semantic_assessment
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="finish 中的语义评估与当前 Evidence 已接受的评估不一致。",
            )
        try:
            premise_result = validate_premise_gap_assessment(
                ctx.requirement,
                ctx.evidences(),
                request.semantic_assessment,
                current_result=ctx.premise_result,
            )
        except ValueError as exc:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"Evidence Gap 评估校验失败：{exc}",
            ) from exc
        effective_premise_result = premise_result or ctx.premise_result
        if (
            effective_premise_result is not None
            and effective_premise_result.get("status") == "not_supported"
            and request.reason is not ResearchCompletionReason.PREMISE_NOT_SUPPORTED
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="Evidence 已否定用户前提，必须使用 premise_not_supported 收口。",
            )
        if (
            request.reason is ResearchCompletionReason.PREMISE_NOT_SUPPORTED
            and (
                effective_premise_result is None
                or effective_premise_result.get("status") != "not_supported"
            )
        ):
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="当前 Evidence 没有否定用户前提，不能使用 premise_not_supported。",
            )
        status = RESEARCH_COMPLETION_STATUS_BY_REASON[request.reason]
        evidences = ctx.evidences()
        try:
            completion = ResearchCompletion.model_validate(
                {
                    "run_id": ctx.run_id,
                    "status": status,
                    "reason": request.reason,
                    "summary": request.summary,
                    "claims": request.claims,
                    "evidence_ids": request.evidence_ids,
                    "limitations": request.limitations,
                }
            )
            completion.validate_evidence(evidences)
        except (ValidationError, ValueError) as exc:
            message = str(exc)
            code = (
                ToolErrorCode.EVIDENCE_REFERENCE_INVALID
                if "EVIDENCE_NOT_FOUND" in message or "CROSS_RUN" in message
                else ToolErrorCode.INVALID_REQUEST
            )
            raise _fail(
                ctx,
                self.name,
                code=code,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=f"结束请求校验失败：{message.split('Value error, ')[-1]}",
                details={"reason": request.reason.value, "status": status},
            ) from exc
        # §10.3.2：充分结论必须先满足服务端最低结构门槛；该门槛是
        # 必要条件，不替代模型对 Evidence 实际内容的充分性判断。
        if request.reason is ResearchCompletionReason.SUFFICIENT_EVIDENCE:
            evaluation = evaluate_structural_coverage(
                ctx.requirement,
                ctx.analysis_evidences(),
                premise_result=effective_premise_result,
            )
            if not evaluation.minimum_requirements_met:
                raise _fail(
                    ctx,
                    self.name,
                    code=ToolErrorCode.INVALID_REQUEST,
                    stage=ToolFailureStage.VALIDATION,
                    parameter_retryable=False,
                    message="finish 过早：最低结构要求尚未覆盖，不能提交充分结论。",
                    details={
                        "gaps": list(evaluation.gap_messages()),
                        "target_metric_coverage": dict(
                            evaluation.target_metric_coverage
                        ),
                    },
                )
        # §10.3.1：假设评估经服务端裁决——证据归属、invalid 保留权、降级。
        try:
            hypothesis_result = evaluate_hypothesis_assessments(
                request.hypothesis_assessments, evidences
            )
        except HypothesisEvaluationError as exc:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST
                if exc.code == "RESEARCH_AGENT_HYPOTHESIS_EVIDENCE_REQUIRED"
                else ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message=str(exc),
                details={"reason_code": exc.code},
            ) from exc
        if hypothesis_result.violations:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=False,
                message="假设评估被服务端拒绝。",
                details={"violations": list(hypothesis_result.violations)},
            )
        # §10.3.4：报告硬门禁。任何违规都让 finish 整体失败，不允许删掉
        # 引用后继续输出原结论。
        violations = validate_report_conclusions(
            run_id=ctx.run_id,
            summary=request.summary,
            summary_evidence_ids=request.evidence_ids,
            summary_limitations=request.limitations,
            findings=request.findings,
            claims=request.claims,
            evidences=evidences,
            assessments=hypothesis_result.assessments,
        )
        if violations:
            raise _fail(
                ctx,
                self.name,
                code=ToolErrorCode.INVALID_REQUEST,
                stage=ToolFailureStage.VALIDATION,
                parameter_retryable=True,
                message="结论校验失败：存在无法溯源或超出证据强度的表述。",
                details={"violations": list(violations)},
                corrections=tuple(violations[:10]),
            )
        ctx.record_hypothesis_assessments(hypothesis_result.assessments)
        ctx.set_hypothesis_audit(hypothesis_result.audit)
        ctx.set_report_inputs(
            {
                "findings": [item.model_dump(mode="json") for item in request.findings],
                "claims": [item.model_dump(mode="json") for item in request.claims],
            }
        )
        if premise_result is not None:
            ctx.set_premise_result(premise_result)
        ctx.set_semantic_assessment(request.semantic_assessment)
        ctx.finish(completion)
        return _success(
            ctx,
            self.name,
            message=f"研究结束（{status}）：{request.summary[:200]}",
            evidence_ids=tuple(request.evidence_ids),
            statistics={
                "claims": len(request.claims),
                "findings": len(request.findings),
                "hypothesis_assessments": len(request.hypothesis_assessments),
                "hypothesis_downgrades": sum(
                    1 for record in hypothesis_result.audit if record.downgraded
                ),
                "unanswered_questions": len(request.unanswered_questions),
            },
        )


def _safe_sort(
    rows: list[dict[str, Any]],
    field: str,
    desc: bool,
) -> list[dict[str, Any]]:
    """确定性排序：NULL 永远排在最后；类型混排时退化为字符串比较。"""

    def key(row: dict[str, Any]) -> tuple[bool, Any]:
        value = row.get(field)
        return (value is None, value)

    try:
        return sorted(rows, key=key, reverse=desc)
    except TypeError:
        return sorted(
            rows,
            key=lambda row: (row.get(field) is None, str(row.get(field))),
            reverse=desc,
        )


def _bounded_rows(
    page: list[dict[str, Any]],
    fields: list[str],
    max_chars: int,
) -> tuple[list[dict[str, Any]], bool]:
    """按字符预算投影行，防止完整大结果进入 Prompt 和 Trace。"""

    projected: list[dict[str, Any]] = []
    used = 0
    for row in page:
        item = {field: row.get(field) for field in fields}
        size = len(json.dumps(item, ensure_ascii=False, default=str))
        if projected and used + size > max_chars:
            return projected, True
        projected.append(item)
        used += size
    return projected, False


def build_research_tool_registry(
    *,
    middlewares: list[Any] | None = None,
) -> ToolRegistry:
    """只注册 Research 所需工具，不暴露任何直接 SQL 工具。"""

    registry = ToolRegistry(middlewares=middlewares)
    for tool in (
        QuerySemanticDataTool(),
        InspectEvidenceTool(),
        ComputeEvidenceTool(),
        SubmitResearchPlanTool(),
        FinishResearchTool(),
    ):
        registry.register(tool)
    return registry


__all__ = [
    "RESEARCH_TOOL_NAMES",
    "SubmitResearchPlanArgs",
    "SubmitResearchPlanTool",
    "ComputeEvidenceArgs",
    "ComputeEvidenceTool",
    "FinishResearchArgs",
    "FinishResearchTool",
    "InspectEvidenceArgs",
    "InspectEvidenceTool",
    "QuerySemanticDataArgs",
    "QuerySemanticDataTool",
    "build_research_tool_registry",
]
