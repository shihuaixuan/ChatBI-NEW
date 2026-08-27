"""阶段 4 的三个 Research 数据工具。

本模块只负责把新 ReAct 工具契约接到已有的语义查询、计算和结果存储服务：

- ``query_semantic_data`` 调用受治理的 ``SemanticQueryRuntime``；
- ``compute_evidence`` 调用白名单 ``ComputeEngine`` 任务构造逻辑；
- ``read_evidence_rows`` 只通过当前 Run 的 Evidence 映射读取完整结果。

完整结果始终留在 ResultStore，模型可见的 ``Evidence`` 和 ``EvidenceRows`` 只
包含有界行、统计和逻辑列信息。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, cast

from pydantic import ValidationError

from apps.chatbi.errors import (
    ResearchToolExecutionError,
    ResultArtifactReadError,
    ResultArtifactWriteError,
)
from apps.chatbi.models.dto.analysis_plan import (
    ResultSetKind,
    ResultSetRef,
)
from apps.chatbi.models.dto.research_agent import (
    ComputeEvidenceAction,
    ComputeEvidenceArguments,
    Evidence,
    EvidenceColumn,
    EvidenceComparison,
    EvidenceComputation,
    EvidenceData,
    EvidenceDefinition,
    EvidenceFilter,
    EvidenceLimitation,
    EvidenceRows,
    QuerySemanticDataAction,
    QuerySemanticDataArguments,
    ReadEvidenceRowsAction,
    ReadEvidenceRowsArguments,
    ResearchActionType,
    ResearchComputeOperation,
    ResearchComputeRequest,
    ResearchEvidence,
    ResearchEvidenceDependency,
    ResearchEvidenceStatistics,
    ResearchEvidenceValueRef,
    ResearchLiteralFilter,
    ResearchLogicalColumn,
    ResearchOrder,
    ResearchOrderDirection,
    ResearchQueryComparison,
    ResearchResultRef,
    ResearchRowSelector,
    ResearchSemanticQuery,
    ResearchTimeRole,
    ResearchToolCallRef,
    TimeRange,
    ToolErrorCode,
)
from apps.chatbi.orchestration.agent.tools.research import (
    ComputeEvidenceTool as LegacyComputeEvidenceTool,
)
from apps.chatbi.orchestration.agent.tools.research import (
    ResearchToolObservationFailure,
)
from apps.chatbi.services.research.action_fingerprint import (
    research_action_fingerprint,
)
from apps.chatbi.services.research.ports import (
    PreparedResearchAction,
    ResearchTool,
    ResearchToolCostEstimate,
)
from apps.chatbi.services.research.runtime import ResearchToolRegistry
from apps.chatbi.services.research.semantic_runtime import (
    semantic_query_plan_id,
)
from apps.chatbi.services.research.tool_context import ResearchToolContext
from apps.conversation import ChatRecordExecutionType

ValueRole = Literal[
    "value",
    "current",
    "previous",
    "difference",
    "growth_rate",
    "share",
    "contribution",
]


class QuerySemanticDataResearchTool(
    ResearchTool[QuerySemanticDataArguments, Evidence, ResearchSemanticQuery]
):
    """执行声明式语义查询并生成新的 Evidence。"""

    name = ResearchActionType.QUERY_SEMANTIC_DATA
    args_model = QuerySemanticDataArguments
    result_model = Evidence
    parallel_safe = True

    def prepare(
        self,
        context: ResearchToolContext,
        args: QuerySemanticDataArguments,
    ) -> PreparedResearchAction[QuerySemanticDataArguments, ResearchSemanticQuery]:
        if args.result.limit > 1_000:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PLANNING_FAILED,
                "语义查询单次最多返回 1000 行，请缩小 limit 或使用 read_evidence_rows 分页读取",
                retryable=True,
                parameter_retryable=True,
            )
        query = _build_semantic_query(context, args)
        try:
            context.requirement.validate_query(query, context.evidences())
        except ValueError as exc:
            raise _query_validation_error(exc) from exc
        fingerprint = research_action_fingerprint(
            QuerySemanticDataAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(query_calls=1, wall_time_ms=1_000),
            domain=query,
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[QuerySemanticDataArguments, ResearchSemanticQuery],
    ) -> Evidence:
        if context.semantic_runtime is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "语义查询 Runtime 未配置",
            )
        query = prepared.domain
        if query is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "语义查询准备结果缺少查询对象",
            )
        purpose = prepared.purpose or "语义查询"
        query = query.model_copy(update={"purpose": purpose})
        # 计划指纹不包含 purpose，保证相同参数只执行一次。
        stable_query = query.model_copy(update={"purpose": "query_semantic_data"})
        outcome = context.semantic_runtime.execute(
            context,
            query,
            requirement=context.requirement,
            evidence=context.evidences(),
            plan_id=semantic_query_plan_id(stable_query),
            iteration=context.evidence_iteration,
        )
        if outcome.status != "succeeded" or not outcome.evidence:
            raise _semantic_outcome_error(outcome)
        legacy = outcome.evidence[0]
        result_id = outcome.primary_result_id or legacy.result_ref.result_id
        if context.result_set_payload(result_id) is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERSISTENCE_FAILED,
                f"查询结果集 {result_id} 缺少内部结果映射",
                retryable=True,
                same_parameter_retryable=True,
            )
        evidence_id = Evidence.evidence_id_for_tool_call(
            prepared.tool_call_id or prepared.action_fingerprint
        )
        evidence = _evidence_from_legacy(
            legacy,
            evidence_id=evidence_id,
            purpose=purpose,
            definition=_query_definition(prepared.args),
        )
        context.register_evidence(
            legacy.model_copy(
                update={
                    "evidence_id": evidence_id,
                    "purpose": purpose,
                    "source_tool_call": ResearchToolCallRef(
                        run_id=context.run_id,
                        tool_call_id=prepared.tool_call_id
                        or prepared.action_fingerprint,
                    ),
                }
            )
        )
        context.record_research_evidence(evidence, result_id=result_id)
        return evidence


class ComputeEvidenceResearchTool(
    ResearchTool[ComputeEvidenceArguments, Evidence, ResearchComputeRequest]
):
    """对当前 Run 的 Evidence 执行白名单确定性计算。"""

    name = ResearchActionType.COMPUTE_EVIDENCE
    args_model = ComputeEvidenceArguments
    result_model = Evidence
    # 计算会写入 ResultStore 和 Evidence 台账，存在依赖时必须串行。
    parallel_safe = False

    def prepare(
        self,
        context: ResearchToolContext,
        args: ComputeEvidenceArguments,
    ) -> PreparedResearchAction[ComputeEvidenceArguments, ResearchComputeRequest]:
        request = ResearchComputeRequest(
            run_id=context.run_id,
            operation=ResearchComputeOperation(args.operation),
            input_evidence_ids=args.input_evidence_ids,
            metric_refs=args.metric_refs,
            dimension_refs=args.dimension_refs,
            group_by_refs=args.group_by_refs,
            order=tuple(
                ResearchOrder(
                    ref=item.field_ref,
                    value_role=_value_role(item.value_role),
                    direction=ResearchOrderDirection(item.direction),
                )
                for item in args.order_by
            ),
            limit=args.limit,
            tolerance=args.tolerance,
        )
        for evidence_id in request.input_evidence_ids:
            if context.research_evidence(evidence_id) is None:
                raise ResearchToolExecutionError(
                    ResearchToolExecutionError.ARGUMENTS_INVALID,
                    f"输入 Evidence {evidence_id} 不属于当前 Run",
                    retryable=True,
                    parameter_retryable=True,
                )
            if context.research_evidence_result_id(evidence_id) is None:
                raise ResearchToolExecutionError(
                    ResearchToolExecutionError.PERSISTENCE_FAILED,
                    f"Evidence {evidence_id} 缺少内部结果映射",
                    retryable=True,
                    same_parameter_retryable=True,
                )
        fingerprint = research_action_fingerprint(
            ComputeEvidenceAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(compute_calls=1, wall_time_ms=500),
            domain=request,
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[ComputeEvidenceArguments, ResearchComputeRequest],
    ) -> Evidence:
        request = prepared.domain
        if request is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "计算准备结果缺少计算请求",
            )
        legacy_inputs = tuple(
            _legacy_evidence_from_react(context, evidence_id)
            for evidence_id in request.input_evidence_ids
        )
        helper = LegacyComputeEvidenceTool()
        try:
            task = helper._build_task(
                context,
                request,
                list(legacy_inputs),
                prepared.action_fingerprint,
            )
            rows, fields, sql = helper._execute_plan(
                context,
                request,
                list(legacy_inputs),
                task,
            )
        except ResearchToolObservationFailure as exc:
            raise _legacy_observation_error(exc.observation) from exc
        result_ref = _register_compute_result(
            context,
            request,
            prepared.action_fingerprint,
            fields,
            rows,
            sql,
        )
        logical_columns = helper._derived_columns(
            request,
            list(legacy_inputs),
            fields,
        )
        evidence_id = Evidence.evidence_id_for_tool_call(
            prepared.tool_call_id or prepared.action_fingerprint
        )
        evidence = _computed_evidence(
            context,
            request,
            evidence_id=evidence_id,
            fields=fields,
            rows=rows,
            logical_columns=logical_columns,
            purpose=prepared.purpose or f"{request.operation.value} 计算",
        )
        context.record_research_evidence(
            evidence,
            result_id=result_ref.result_set_id,
        )
        # 兼容阶段 3语义查询的 EvidenceSelector，同时保留新 Evidence 作为规范结果。
        iteration = context.advance_evidence_iteration(
            minimum=max(item.iteration for item in legacy_inputs) + 1
        )
        context.register_evidence(
            _legacy_evidence_from_react(
                context,
                evidence_id,
                iteration=iteration,
                dependencies=tuple(
                    ResearchEvidenceDependency(
                        evidence_id=item.evidence_id,
                        run_id=context.run_id,
                        source_iteration=item.iteration,
                        relation=request.operation.value,
                    )
                    for item in legacy_inputs
                ),
            )
        )
        return evidence


class ReadEvidenceRowsResearchTool(
    ResearchTool[ReadEvidenceRowsArguments, EvidenceRows, tuple[Evidence, str]]
):
    """按逻辑列、稳定排序和分页读取已有 Evidence。"""

    name = ResearchActionType.READ_EVIDENCE_ROWS
    args_model = ReadEvidenceRowsArguments
    result_model = EvidenceRows
    parallel_safe = True

    def prepare(
        self,
        context: ResearchToolContext,
        args: ReadEvidenceRowsArguments,
    ) -> PreparedResearchAction[ReadEvidenceRowsArguments, tuple[Evidence, str]]:
        evidence = context.research_evidence(args.evidence_id)
        if evidence is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"Evidence {args.evidence_id} 不属于当前 Run",
                retryable=True,
                parameter_retryable=True,
            )
        result_id = context.research_evidence_result_id(args.evidence_id)
        if result_id is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERSISTENCE_FAILED,
                f"Evidence {args.evidence_id} 缺少内部结果映射",
                retryable=True,
                same_parameter_retryable=True,
            )
        _validate_read_columns(evidence, args)
        fingerprint = research_action_fingerprint(
            ReadEvidenceRowsAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(),
            domain=(evidence, result_id),
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[ReadEvidenceRowsArguments, tuple[Evidence, str]],
    ) -> EvidenceRows:
        domain = prepared.domain
        if domain is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "读取准备结果缺少 Evidence 映射",
            )
        evidence, result_id = domain
        result_store = context.result_store
        if result_store is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERSISTENCE_FAILED,
                "ResultStore 未配置",
                retryable=True,
                same_parameter_retryable=True,
            )
        try:
            payload = context.result_set_payload(result_id)
            if payload is None:
                raise ValueError("结果集引用不存在")
            ref = ResultSetRef.model_validate(payload)
            execution_id, chat_id, record_id, _ = context.execution_identity()
            snapshot = result_store.read(
                ref,
                execution_id=execution_id,
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=chat_id,
                record_id=record_id,
            )
        except (ResultArtifactReadError, ValidationError, TypeError, ValueError) as exc:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERSISTENCE_FAILED,
                f"结果集读取失败：{exc}",
                retryable=True,
                same_parameter_retryable=True,
            ) from exc
        rows = [dict(row) for row in snapshot.rows]
        args = prepared.args
        rows = _sort_rows(rows, evidence, args)
        selected_columns = _selected_columns(evidence, args.column_refs)
        fields = tuple(column.name for column in selected_columns)
        page = rows[args.offset : args.offset + args.limit]
        result_rows = tuple(
            tuple(row.get(field) for field in fields) for row in page
        )
        max_rows = context.budget.max_evidence_rows
        truncated = len(result_rows) > max_rows
        if truncated:
            result_rows = result_rows[:max_rows]
        return EvidenceRows(
            evidence_id=evidence.evidence_id,
            columns=tuple(selected_columns),
            rows=result_rows,
            total_row_count=snapshot.ref.row_count,
            offset=args.offset,
            truncated=truncated or args.offset + len(result_rows) < snapshot.ref.row_count,
        )


def build_research_data_tool_registry() -> ResearchToolRegistry:
    """创建阶段 4数据工具白名单。"""

    registry = ResearchToolRegistry()
    registry.register(QuerySemanticDataResearchTool())
    registry.register(ComputeEvidenceResearchTool())
    registry.register(ReadEvidenceRowsResearchTool())
    return registry


# 提供短名称，便于编排层按工具名称直接导入。
QuerySemanticDataTool = QuerySemanticDataResearchTool
ComputeEvidenceTool = ComputeEvidenceResearchTool
ReadEvidenceRowsTool = ReadEvidenceRowsResearchTool


def _build_semantic_query(
    context: ResearchToolContext,
    args: QuerySemanticDataArguments,
) -> ResearchSemanticQuery:
    time_roles = (
        tuple(ResearchTimeRole(item.role) for item in args.time.periods)
        if args.time is not None
        else (ResearchTimeRole.SINGLE,)
    )
    comparison = ResearchQueryComparison.NONE
    if args.comparison is not None:
        if "growth_rate" in args.comparison.outputs:
            comparison = ResearchQueryComparison.GROWTH_RATE
        elif "difference" in args.comparison.outputs:
            comparison = ResearchQueryComparison.DIFFERENCE
    analysis: Literal[
        "compare",
        "breakdown",
        "drilldown",
        "filter_from_result",
        "contribution",
        "exploration",
    ] = (
        "compare"
        if comparison is not ResearchQueryComparison.NONE
        else "breakdown"
        if args.dimensions
        else "exploration"
    )
    filters: list[ResearchLiteralFilter] = []
    evidence_value_filters: list[ResearchEvidenceValueRef] = []
    for item in args.filters:
        if item.evidence_selector is None:
            filters.append(
                ResearchLiteralFilter(
                    target_ref=item.field_ref,
                    operator=item.operator,
                    value=item.value,
                )
            )
            continue
        selector = item.evidence_selector
        if selector.selection == "all":
            filters.append(
                ResearchLiteralFilter(
                    target_ref=item.field_ref,
                    operator="in",
                    value=_evidence_selector_values(context, selector),
                )
            )
            continue
        evidence_value_filters.append(
            ResearchEvidenceValueRef(
                run_id=context.run_id,
                evidence_id=selector.evidence_id,
                target_ref=item.field_ref,
                column_ref=selector.column_ref,
                row_selector=ResearchRowSelector(
                    rank=1,
                    direction=(
                        ResearchOrderDirection.DESC
                        if selector.selection == "top"
                        else ResearchOrderDirection.ASC
                    ),
                    order_by=selector.column_ref,
                ),
            )
        )
    if args.time is not None:
        expected_time_dimensions = {
            binding.dimension_ref
            for binding in context.requirement.time_bindings
            if binding.role.value in time_roles
        }
        if expected_time_dimensions and args.time.dimension_ref not in expected_time_dimensions:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "时间维度不属于当前 Research Run 的冻结时间绑定",
                retryable=True,
                parameter_retryable=True,
            )
    return ResearchSemanticQuery(
        run_id=context.run_id,
        scope_fingerprint=context.requirement.scope.scope_fingerprint,
        version_snapshot=context.requirement.version_snapshot,
        metrics=args.metrics,
        dimensions=args.dimensions,
        time_grain=args.time.grain if args.time is not None else None,
        time_ranges=time_roles,
        filters=tuple(filters),
        evidence_value_filters=tuple(evidence_value_filters),
        comparison=comparison,
        analysis=analysis,
        order=tuple(
            ResearchOrder(
                ref=item.field_ref,
                value_role=_value_role(item.value_role),
                direction=ResearchOrderDirection(item.direction),
            )
            for item in args.result.order_by
        ),
        limit=min(args.result.limit, 1_000),
        purpose="query_semantic_data",
    )


def _query_definition(args: QuerySemanticDataArguments) -> EvidenceDefinition:
    time_ranges: tuple[TimeRange, ...] = ()
    if args.time is not None:
        time_ranges = tuple(
            TimeRange(
                start=item.start,
                end=item.end,
                granularity=args.time.grain,
            )
            for item in args.time.periods
        )
    comparison = None
    if args.comparison is not None:
        comparison = EvidenceComparison(
            base_period=args.comparison.base_period,
            against_period=args.comparison.against_period,
            outputs=args.comparison.outputs,
        )
    filters = tuple(
        EvidenceFilter(
            field_ref=item.field_ref,
            operator=item.operator,
            value=item.value,
        )
        for item in args.filters
        if item.evidence_selector is None
    )
    return EvidenceDefinition(
        metrics=args.metrics,
        dimensions=args.dimensions,
        time_ranges=time_ranges,
        filters=filters,
        comparison=comparison,
    )


def _evidence_selector_values(
    context: ResearchToolContext,
    selector: Any,
) -> list[str | int | float | bool]:
    """从当前 Run 的完整结果中解析 EvidenceSelector 的全部值。"""

    source = context.evidence(selector.evidence_id)
    if source is None or source.run_id != context.run_id:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"Evidence {selector.evidence_id} 不属于当前 Run",
            retryable=True,
            parameter_retryable=True,
        )
    fields = [
        column.result_field
        for column in source.logical_columns
        if column.asset_ref == selector.column_ref and column.result_field
    ]
    if len(fields) != 1:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"Evidence 列 {selector.column_ref} 无法唯一映射到结果字段",
            retryable=True,
            parameter_retryable=True,
        )
    result_id = source.result_ref.result_id
    payload = context.result_set_payload(result_id)
    result_store = context.result_store
    if payload is None or result_store is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            f"Evidence {selector.evidence_id} 缺少完整结果映射",
            retryable=True,
            same_parameter_retryable=True,
        )
    try:
        ref = ResultSetRef.model_validate(payload)
        execution_id, chat_id, record_id, _ = context.execution_identity()
        snapshot = result_store.read(
            ref,
            execution_id=execution_id,
            execution_type=ChatRecordExecutionType.AGENT,
            chat_id=chat_id,
            record_id=record_id,
        )
    except (ResultArtifactReadError, ValidationError, TypeError, ValueError) as exc:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            f"EvidenceSelector 读取结果失败：{exc}",
            retryable=True,
            same_parameter_retryable=True,
        ) from exc
    field = fields[0]
    if field not in snapshot.ref.fields:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.RESULT_INVALID,
            f"Evidence 列 {selector.column_ref} 不在结果集字段中",
        )
    values: list[str | int | float | bool] = []
    for row in snapshot.rows:
        value = _scalar(row.get(field))
        if value is not None and value not in values:
            values.append(value)
    if not values:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"Evidence 列 {selector.column_ref} 没有可用于筛选的值",
            retryable=True,
            parameter_retryable=True,
        )
    return values


def _evidence_from_legacy(
    legacy: ResearchEvidence,
    *,
    evidence_id: str,
    purpose: str,
    definition: EvidenceDefinition,
) -> Evidence:
    columns = tuple(_column_from_legacy(item) for item in legacy.logical_columns)
    fields = tuple(item.name for item in columns)
    rows = tuple(
        tuple(_scalar(row.get(field)) for field in fields)
        for row in legacy.sample_rows
    )
    limitations = tuple(
        EvidenceLimitation(
            code="query_limited",
            description=value,
            impact="结果仅展示受控样本，完整行保存在 ResultStore",
        )
        for value in legacy.limitations
    )
    return Evidence(
        evidence_id=evidence_id,
        evidence_type="query_result",
        purpose=purpose,
        definition=definition,
        columns=columns,
        data=EvidenceData(
            row_count=legacy.statistics.row_count,
            truncated=len(rows) < legacy.statistics.row_count,
            rows=rows,
        ),
        limitations=limitations,
    )


def _computed_evidence(
    context: ResearchToolContext,
    request: ResearchComputeRequest,
    *,
    evidence_id: str,
    fields: list[str],
    rows: list[dict[str, Any]],
    logical_columns: tuple[ResearchLogicalColumn, ...],
    purpose: str,
) -> Evidence:
    columns = tuple(_column_from_legacy(item) for item in logical_columns)
    if len(columns) != len(fields) or tuple(item.name for item in columns) != tuple(fields):
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.RESULT_INVALID,
            "计算结果缺少完整的逻辑列映射",
        )
    sample_rows = rows[: context.budget.max_evidence_rows]
    definition = EvidenceDefinition(
        metrics=request.metric_refs,
        dimensions=tuple(dict.fromkeys((*request.dimension_refs, *request.group_by_refs))),
        computation=EvidenceComputation(
            operation=request.operation.value,
            input_evidence_ids=request.input_evidence_ids,
            parameters=_compute_parameters(request),
        ),
    )
    return Evidence(
        evidence_id=evidence_id,
        evidence_type="computation_result",
        purpose=purpose,
        definition=definition,
        columns=columns,
        data=EvidenceData(
            row_count=len(rows),
            truncated=len(sample_rows) < len(rows),
            rows=tuple(
                tuple(_scalar(row.get(field)) for field in fields)
                for row in sample_rows
            ),
        ),
        parent_evidence_ids=request.input_evidence_ids,
    )


def _register_compute_result(
    context: ResearchToolContext,
    request: ResearchComputeRequest,
    fingerprint: str,
    fields: list[str],
    rows: list[dict[str, Any]],
    sql: str | None,
) -> ResultSetRef:
    if context.result_store is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            "ResultStore 未配置",
            retryable=True,
            same_parameter_retryable=True,
        )
    execution_id, chat_id, record_id, _ = context.execution_identity()
    try:
        ref = context.result_store.register(
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
            idempotency_key=f"research-react-compute:{context.run_id}:{fingerprint}",
        )
    except (ResultArtifactWriteError, ValueError, TypeError) as exc:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            f"计算结果保存失败：{exc}",
            retryable=True,
            same_parameter_retryable=True,
        ) from exc
    context.merge_result_ref(ref)
    return ref


def _legacy_evidence_from_react(
    context: ResearchToolContext,
    evidence_id: str,
    *,
    iteration: int | None = None,
    dependencies: tuple[ResearchEvidenceDependency, ...] = (),
) -> ResearchEvidence:
    existing = context.evidence(evidence_id)
    if existing is not None:
        return existing
    evidence = context.research_evidence(evidence_id)
    result_id = context.research_evidence_result_id(evidence_id)
    if evidence is None or result_id is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"Evidence {evidence_id} 不属于当前 Run",
            retryable=True,
            parameter_retryable=True,
        )
    logical_columns = tuple(
        ResearchLogicalColumn(
            asset_ref=column.semantic_ref or f"METRIC:computed:{index}",
            value_role=("group_key" if column.role == "dimension" else "value"),
            result_field=column.name,
        )
        for index, column in enumerate(evidence.columns)
    )
    rows = [
        {
            column.name: value
            for column, value in zip(evidence.columns, row, strict=True)
        }
        for row in evidence.data.rows
    ]
    legacy = ResearchEvidence(
        run_id=context.run_id,
        evidence_id=evidence_id,
        source_tool_call=ResearchToolCallRef(
            run_id=context.run_id,
            tool_call_id=evidence_id.removeprefix("evidence:"),
        ),
        result_ref=ResearchResultRef(run_id=context.run_id, result_id=result_id),
        iteration=0 if iteration is None else iteration,
        purpose=evidence.purpose,
        metric_refs=evidence.definition.metrics,
        dimension_refs=evidence.definition.dimensions,
        logical_columns=logical_columns,
        statistics=ResearchEvidenceStatistics(row_count=evidence.data.row_count),
        sample_rows=tuple(rows),
        dependencies=dependencies,
        version_snapshot=context.requirement.version_snapshot,
    )
    return legacy


def _selected_columns(evidence: Evidence, refs: tuple[str, ...]) -> tuple[EvidenceColumn, ...]:
    selected = tuple(
        column
        for ref in refs
        for column in evidence.columns
        if column.semantic_ref == ref
    )
    if not selected:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            "读取列不属于当前 Evidence",
            retryable=True,
            parameter_retryable=True,
        )
    return selected


def _validate_read_columns(evidence: Evidence, args: ReadEvidenceRowsArguments) -> None:
    _selected_columns(evidence, args.column_refs)
    known_refs = {column.semantic_ref for column in evidence.columns}
    for item in args.order_by:
        _value_role(item.value_role)
        if item.field_ref not in known_refs:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"排序列 {item.field_ref} 不属于当前 Evidence",
                retryable=True,
                parameter_retryable=True,
            )


def _sort_rows(
    rows: list[dict[str, Any]],
    evidence: Evidence,
    args: ReadEvidenceRowsArguments,
) -> list[dict[str, Any]]:
    result = rows
    for item in reversed(args.order_by):
        field = _evidence_order_field(evidence, item.field_ref, item.value_role)
        if field is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"排序列 {item.field_ref} 不属于当前 Evidence",
                retryable=True,
                parameter_retryable=True,
            )
        try:
            result.sort(
                key=lambda row: (row.get(field) is None, row.get(field)),
                reverse=item.direction == "desc",
            )
        except TypeError as exc:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"排序列 {item.field_ref} 的值类型不一致",
                retryable=True,
                parameter_retryable=True,
            ) from exc
    return result


def _column_from_legacy(column: ResearchLogicalColumn) -> EvidenceColumn:
    role: Literal["dimension", "metric", "computed"] = (
        "dimension" if column.asset_ref.startswith("DIMENSION:") else "metric"
    )
    if column.value_role not in {"group_key", "value"}:
        role = "computed"
    name = column.result_field or column.asset_ref
    return EvidenceColumn(
        name=name,
        semantic_ref=column.asset_ref,
        role=role,
        data_type="unknown",
    )


def _value_role(value: str) -> ValueRole:
    allowed = {
        "value",
        "current",
        "previous",
        "difference",
        "growth_rate",
        "share",
        "contribution",
    }
    if value not in allowed:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"结果排序角色 {value} 不受支持",
            retryable=True,
            parameter_retryable=True,
        )
    return cast(ValueRole, value)


def _compute_parameters(
    request: ResearchComputeRequest,
) -> tuple[tuple[str, str | int | float | bool | None], ...]:
    values: list[tuple[str, str | int | float | bool | None]] = []
    if request.limit is not None:
        values.append(("limit", request.limit))
    if request.tolerance is not None:
        values.append(("tolerance", request.tolerance))
    return tuple(values)


def _semantic_outcome_error(outcome: Any) -> ResearchToolExecutionError:
    code = (
        outcome.error_code.value
        if isinstance(outcome.error_code, ToolErrorCode)
        else str(outcome.error_code or ToolErrorCode.EXECUTION_FAILED.value)
    )
    return ResearchToolExecutionError(
        code,
        outcome.message or "语义查询失败",
        retryable=outcome.retryable,
        parameter_retryable=outcome.parameter_retryable,
        same_parameter_retryable=outcome.same_parameter_retryable,
    )


def _query_validation_error(error: ValueError) -> ResearchToolExecutionError:
    """保留查询校验失败的权限、引用和参数边界，而不是统一吞成 prepare 失败。"""

    code = str(error)
    if code.endswith("_OUT_OF_SCOPE") or code in {
        "RESEARCH_AGENT_QUERY_SCOPE_MISMATCH",
        "RESEARCH_AGENT_QUERY_FILTER_OUT_OF_SCOPE",
    }:
        mapped = ToolErrorCode.SCOPE_DENIED.value
    elif "EVIDENCE" in code.upper():
        mapped = ToolErrorCode.EVIDENCE_REFERENCE_INVALID.value
    else:
        mapped = ToolErrorCode.INVALID_REQUEST.value
    return ResearchToolExecutionError(
        mapped,
        code or "查询参数校验失败",
        retryable=True,
        parameter_retryable=True,
    )


def _evidence_order_field(
    evidence: Evidence,
    field_ref: str,
    value_role: str,
) -> str | None:
    """按逻辑引用和值角色选择结果字段，支持计算结果的多个派生列。"""

    candidates = [
        column
        for column in evidence.columns
        if column.semantic_ref == field_ref
    ]
    if not candidates:
        return None
    if value_role == "group_key":
        for column in candidates:
            if column.role == "dimension":
                return column.name
    if value_role in {"value", "current", "previous", "difference", "growth_rate", "share", "contribution"}:
        suffix = {
            "current": "_current",
            "previous": "_previous",
            "difference": "_difference",
            "growth_rate": "_growth_rate",
            "share": "_share",
            "contribution": "_contribution",
        }.get(value_role)
        if suffix is not None:
            for column in candidates:
                if column.name.endswith(suffix) or column.name == value_role:
                    return column.name
        for column in candidates:
            if column.role in {"metric", "computed"}:
                return column.name
    return candidates[0].name


def _legacy_observation_error(observation: Any) -> ResearchToolExecutionError:
    code = (
        observation.error_code.value
        if isinstance(observation.error_code, ToolErrorCode)
        else str(observation.error_code or ToolErrorCode.EXECUTION_FAILED.value)
    )
    return ResearchToolExecutionError(
        code,
        observation.message or "计算失败",
        retryable=observation.retryable,
        parameter_retryable=observation.parameter_retryable,
        same_parameter_retryable=observation.same_parameter_retryable,
    )


def _data_type(rows: Iterable[dict[str, Any]], field: str) -> str:
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int | float):
            return "decimal"
        return "string"
    return "unknown"


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ResearchToolExecutionError(
        ResearchToolExecutionError.RESULT_INVALID,
        "结果包含不支持的非标量值",
    )


__all__ = [
    "ComputeEvidenceResearchTool",
    "ComputeEvidenceTool",
    "QuerySemanticDataResearchTool",
    "QuerySemanticDataTool",
    "ReadEvidenceRowsResearchTool",
    "ReadEvidenceRowsTool",
    "build_research_data_tool_registry",
]
