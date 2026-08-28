"""阶段 4 的三个 Research 数据工具。

本模块只负责把新 ReAct 工具契约接到已有的语义查询、计算和结果存储服务：

- ``query_semantic_data`` 调用受治理的 ``SemanticQueryRuntime``；
- ``compute_evidence`` 调用白名单 ``ComputeEngine`` 任务构造逻辑；
- ``read_evidence_rows`` 只通过当前 Run 的 Evidence 映射读取完整结果。

完整结果始终留在 ResultStore，模型可见的 ``Evidence`` 和 ``EvidenceRows`` 只
包含有界行、统计和逻辑列信息。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any, Literal, cast

from pydantic import ValidationError

from apps.chatbi.errors import (
    ResearchToolExecutionError,
    ResultArtifactReadError,
    ResultArtifactWriteError,
)
from apps.chatbi.models.dto.analysis_plan import (
    ComputeDerivation,
    ComputeOperation,
    ComputeTask,
    ResultSetKind,
    ResultSetRef,
    ResultSetSnapshot,
)
from apps.chatbi.models.dto.research_agent import (
    ClarificationRequest,
    Completion,
    CompletionValidationError,
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
    Finding,
    FinishResearchAction,
    FinishResearchArguments,
    FinishResearchResult,
    MetricAnalysisRelation,
    MetricFormula,
    QuerySemanticDataAction,
    QuerySemanticDataArguments,
    ReadEvidenceRowsAction,
    ReadEvidenceRowsArguments,
    RequestClarificationAction,
    RequestClarificationArguments,
    ResearchActionType,
    ResearchComputeOperation,
    ResearchComputeRequest,
    ResearchEvidence,
    ResearchEvidenceValueRef,
    ResearchLiteralFilter,
    ResearchLogicalColumn,
    ResearchOrder,
    ResearchOrderDirection,
    ResearchQueryComparison,
    ResearchRowSelector,
    ResearchSemanticQuery,
    ResearchStateStatus,
    ResearchTimeRole,
    SearchSemanticAssetsAction,
    SearchSemanticAssetsArguments,
    SemanticContext,
    SemanticContextDelta,
    SemanticDimension,
    SemanticHierarchy,
    SemanticMetric,
    TimeRange,
    ToolErrorCode,
)
from apps.chatbi.services.computation.errors import (
    ComputeEngineError,
    ComputeOperationError,
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
from apps.retrieval import build_retrieval_request

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
        semantic_evidence = outcome.evidence[0]
        result_id = outcome.primary_result_id or semantic_evidence.result_ref.result_id
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
        evidence = _evidence_from_semantic_result(
            semantic_evidence,
            evidence_id=evidence_id,
            purpose=purpose,
            definition=_query_definition(prepared.args),
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
        inputs = tuple(
            context.evidence(evidence_id) for evidence_id in request.input_evidence_ids
        )
        if any(item is None for item in inputs):
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "计算输入 Evidence 不属于当前 Run",
                retryable=True,
                parameter_retryable=True,
            )
        compute_inputs = tuple(item for item in inputs if item is not None)
        task = _build_compute_task(context, request, compute_inputs)
        rows, fields, sql = _execute_compute_plan(context, request, compute_inputs, task)
        result_ref = _register_compute_result(
            context,
            request,
            prepared.action_fingerprint,
            fields,
            rows,
            sql,
        )
        logical_columns = _derived_compute_columns(request, compute_inputs, fields)
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


class SearchSemanticAssetsResearchTool(
    ResearchTool[SearchSemanticAssetsArguments, SemanticContextDelta, Any]
):
    """在冻结权限范围内补充语义资产，并生成新的输入快照。"""

    name = ResearchActionType.SEARCH_SEMANTIC_ASSETS
    args_model = SearchSemanticAssetsArguments
    result_model = SemanticContextDelta
    parallel_safe = False

    def prepare(
        self,
        context: ResearchToolContext,
        args: SearchSemanticAssetsArguments,
    ) -> PreparedResearchAction[SearchSemanticAssetsArguments, Any]:
        if _semantic_search_service(context) is None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "补充语义检索服务未配置",
            )
        _validate_scope_refs(context, args.related_asset_refs)
        retrieval_requests = _supplemental_retrieval_requests(context, args)
        fingerprint = research_action_fingerprint(
            SearchSemanticAssetsAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(
                semantic_search_calls=len(retrieval_requests),
                wall_time_ms=500 * len(retrieval_requests),
            ),
            domain=None,
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[SearchSemanticAssetsArguments, Any],
    ) -> SemanticContextDelta:
        current = context.current_agent_input()
        payload = _execute_semantic_asset_search(context, prepared.args)
        added = _semantic_context_from_search_payload(
            context,
            payload,
            prepared.args,
        )
        new_assets = _semantic_context_difference(current.semantic_context, added)
        merged = _merge_semantic_context(current.semantic_context, new_assets)
        updated = current.model_copy(
            update={
                "agent_input_ref": context.next_agent_input_ref(),
                "semantic_context": merged,
            }
        )
        saved = context.save_agent_input(updated)
        delta = SemanticContextDelta(
            new_agent_input_ref=saved.agent_input_ref or updated.agent_input_ref or "",
            added_semantic_context=new_assets,
        )
        context.record_semantic_context_delta(delta)
        return delta


class RequestClarificationResearchTool(
    ResearchTool[RequestClarificationArguments, ClarificationRequest, Any]
):
    """保存澄清问题并将当前 Research Run 置为等待用户。"""

    name = ResearchActionType.REQUEST_CLARIFICATION
    args_model = RequestClarificationArguments
    result_model = ClarificationRequest
    parallel_safe = False

    def prepare(
        self,
        context: ResearchToolContext,
        args: RequestClarificationArguments,
    ) -> PreparedResearchAction[RequestClarificationArguments, Any]:
        _validate_scope_refs(
            context,
            tuple(
                ref
                for option in args.options
                for ref in option.semantic_refs
            ),
        )
        if context.clarification_request() is not None:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "当前 Research Run 已经存在等待回答的澄清请求",
                retryable=True,
                parameter_retryable=True,
            )
        fingerprint = research_action_fingerprint(
            RequestClarificationAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(wall_time_ms=10),
            domain=None,
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[RequestClarificationArguments, Any],
    ) -> ClarificationRequest:
        tool_call_id = prepared.tool_call_id or prepared.action_fingerprint
        request = ClarificationRequest(
            clarification_request_id=f"clarification:{tool_call_id}",
            question=prepared.args.question,
            options=prepared.args.options,
            allow_free_text=prepared.args.allow_free_text,
        )
        context.save_clarification_request(request)
        return request


class FinishResearchResearchTool(
    ResearchTool[FinishResearchArguments, FinishResearchResult, Any]
):
    """只执行确定性结束校验，并保存通过校验的 Completion。"""

    name = ResearchActionType.FINISH_RESEARCH
    args_model = FinishResearchArguments
    result_model = FinishResearchResult
    parallel_safe = False

    def prepare(
        self,
        context: ResearchToolContext,
        args: FinishResearchArguments,
    ) -> PreparedResearchAction[FinishResearchArguments, Any]:
        fingerprint = research_action_fingerprint(
            FinishResearchAction(purpose="fingerprint", arguments=args),
            version_snapshot=context.requirement.version_snapshot,
        )
        return PreparedResearchAction(
            args=args,
            action_fingerprint=fingerprint,
            cost=ResearchToolCostEstimate(wall_time_ms=10),
            domain=None,
        )

    def execute(
        self,
        context: ResearchToolContext,
        prepared: PreparedResearchAction[FinishResearchArguments, Any],
    ) -> FinishResearchResult:
        errors = _validate_finish_completion(context, prepared.args.completion)
        if errors:
            return FinishResearchResult(
                decision="rejected",
                message="结束请求未通过确定性校验",
                validation_errors=tuple(errors),
            )
        completion = prepared.args.completion
        context.save_react_completion(completion)
        return FinishResearchResult(
            decision="accepted",
            message="结束请求通过校验",
            completion_status=completion.status,
        )


def build_research_data_tool_registry() -> ResearchToolRegistry:
    """创建阶段 4数据工具白名单。"""

    registry = ResearchToolRegistry()
    registry.register(QuerySemanticDataResearchTool())
    registry.register(ComputeEvidenceResearchTool())
    registry.register(ReadEvidenceRowsResearchTool())
    return registry


def build_research_control_tool_registry() -> ResearchToolRegistry:
    """创建阶段 5控制工具白名单。"""

    registry = ResearchToolRegistry()
    registry.register(SearchSemanticAssetsResearchTool())
    registry.register(RequestClarificationResearchTool())
    registry.register(FinishResearchResearchTool())
    return registry


def build_research_tool_registry() -> ResearchToolRegistry:
    """创建阶段 3～5完整 Research 工具白名单。"""

    registry = build_research_data_tool_registry()
    for tool in (
        SearchSemanticAssetsResearchTool(),
        RequestClarificationResearchTool(),
        FinishResearchResearchTool(),
    ):
        registry.register(tool)
    return registry


# 提供短名称，便于编排层按工具名称直接导入。
QuerySemanticDataTool = QuerySemanticDataResearchTool
ComputeEvidenceTool = ComputeEvidenceResearchTool
ReadEvidenceRowsTool = ReadEvidenceRowsResearchTool
SearchSemanticAssetsTool = SearchSemanticAssetsResearchTool
RequestClarificationTool = RequestClarificationResearchTool
FinishResearchTool = FinishResearchResearchTool


def _semantic_search_service(context: ResearchToolContext) -> Any:
    """读取宿主注入的语义检索服务，不创建没有权限快照的默认实例。"""

    service = context.semantic_retrieval_service
    if service is not None:
        return service
    service = getattr(context.context, "semantic_retrieval_service", None)
    if service is not None:
        return service
    return context.context.state.get("semantic_retrieval_service")


def _validate_scope_refs(
    context: ResearchToolContext,
    refs: tuple[str, ...],
) -> None:
    """确保工具提交的语义引用仍属于启动时冻结的 Scope。"""

    allowed = _allowed_semantic_refs(context)
    if any(ref not in allowed for ref in refs):
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERMISSION_DENIED,
            "语义引用不属于当前 Research Run 的冻结权限范围",
            details={"invalid_refs": [ref for ref in refs if ref not in allowed]},
        )


def _allowed_semantic_refs(context: ResearchToolContext) -> set[str]:
    scope = context.requirement.scope
    refs = {
        *scope.target_metric_refs,
        *scope.driver_metric_refs,
        *scope.contribution_metric_refs,
        *scope.dimension_refs,
        *scope.contribution_dimension_refs,
        *scope.allowed_filter_refs,
    }
    return refs - set(scope.excluded_asset_refs)


def _execute_semantic_asset_search(
    context: ResearchToolContext,
    args: SearchSemanticAssetsArguments,
) -> Any:
    """执行一个或两个独立检索通道，并合并返回结果。"""

    service = _semantic_search_service(context)
    if service is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PREPARE_FAILED,
            "补充语义检索服务未配置",
        )
    retrieve = getattr(service, "retrieve", None)
    search = getattr(service, "search", None)
    if not callable(retrieve) and not callable(search):
        search = getattr(service, "search_semantic_assets", None)
    if not callable(retrieve) and not callable(search):
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PREPARE_FAILED,
            "语义检索服务未提供 retrieve 或 search 端口",
        )

    payloads: list[Any] = []
    for request in _supplemental_retrieval_requests(context, args):
        if callable(retrieve):
            result = retrieve(request)
        elif callable(search):
            result = search(request)
        else:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "语义检索服务未提供可调用的检索端口",
            )
        payload = getattr(result, "payload", result)
        if isinstance(payload, SemanticContext):
            payloads.append(payload)
            continue
        if isinstance(payload, Mapping):
            payloads.append(dict(payload))
            continue
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                payloads.append(dumped)
                continue
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.RESULT_INVALID,
            "补充语义检索返回了无法识别的结果",
        )
    return _merge_semantic_search_payloads(payloads)


def _supplemental_retrieval_requests(
    context: ResearchToolContext,
    args: SearchSemanticAssetsArguments,
) -> tuple[Any, ...]:
    """复用初始检索的身份和权限字段，分别构造指标和维度通道。"""

    base = getattr(context.context, "semantic_retrieval_request", None)
    if callable(base):
        base = base()
    metric_requested = any(
        item in {"metric", "metric_formula", "metric_analysis_relation"}
        for item in args.asset_types
    )
    dimension_requested = any(
        item in {"dimension", "hierarchy", "metric_analysis_relation"}
        for item in args.asset_types
    )
    if not metric_requested and not dimension_requested:
        metric_requested = True
    current_question = context.current_agent_input().user_question
    related_names = _related_asset_terms(context, args.related_asset_refs)
    search_text = " ".join(
        item
        for item in (args.query, current_question, *related_names)
        if item and item.strip()
    ).strip()[:4_000]
    query_hash = sha256(search_text.encode()).hexdigest()[:12]

    def build_request(
        metric_phrases: list[str],
        dimension_phrases: list[str],
        channel: str,
    ) -> Any:
        request_id = f"supplement:{context.run_id}:{query_hash}:{channel}"
        if base is not None:
            return base.model_copy(
                update={
                    "request_id": request_id,
                    "metric_phrases": metric_phrases,
                    "dimension_phrases": dimension_phrases,
                }
            )
        if context.context.user_id is None or context.context.user_id <= 0:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERMISSION_DENIED,
                "补充语义检索缺少当前用户身份",
            )
        if context.dataset_id is None or context.dataset_id <= 0:
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PERMISSION_DENIED,
                "补充语义检索缺少当前数据集身份",
            )
        return build_retrieval_request(
            tenant_id=context.workspace_id,
            actor_id=context.context.user_id,
            dataset_id=context.dataset_id,
            metric_phrases=metric_phrases,
            dimension_phrases=dimension_phrases,
            request_id=request_id,
            principal_roles=context.context.principal_roles or None,
            principal_role_ids=context.context.principal_role_ids or None,
            permission_version=context.context.permission_version,
        )

    requests: list[Any] = []
    if metric_requested:
        requests.append(build_request([search_text], [], "metric"))
    if dimension_requested:
        requests.append(build_request([], [search_text], "dimension"))
    return tuple(requests)


def _related_asset_terms(
    context: ResearchToolContext,
    refs: tuple[str, ...],
) -> tuple[str, ...]:
    """将关联资产引用投影为检索词，避免只校验引用而不参与检索。"""

    if not refs:
        return ()
    labels: dict[str, str] = {}
    current = context.current_agent_input().semantic_context
    labels.update({item.ref: item.name for item in current.metrics})
    labels.update({item.ref: item.name for item in current.dimensions})
    schema = _frozen_schema(context)
    if schema is not None:
        for kind, elements in (
            ("METRIC", getattr(schema, "metrics", ())),
            ("DIMENSION", getattr(schema, "dimensions", ())),
        ):
            for raw in elements:
                ref = _schema_element_ref(kind, raw)
                if ref is not None:
                    labels.setdefault(ref, str(_field(raw, "name", ref)))
    return tuple(f"{ref} {labels[ref]}" if ref in labels else ref for ref in refs)


def _merge_semantic_search_payloads(payloads: list[Any]) -> Any:
    """合并双通道结果，保留语义上下文和候选分组两种返回格式。"""

    if not payloads:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.RESULT_INVALID,
            "补充语义检索没有返回结果",
        )
    if len(payloads) == 1:
        return payloads[0]
    contexts: list[SemanticContext] = []
    merged: dict[str, Any] = {}
    for payload in payloads:
        if isinstance(payload, SemanticContext):
            contexts.append(payload)
            continue
        if not isinstance(payload, Mapping):
            continue
        raw_context = payload.get("semantic_context")
        if isinstance(raw_context, SemanticContext):
            contexts.append(raw_context)
        elif isinstance(raw_context, Mapping):
            contexts.append(SemanticContext.model_validate(raw_context))
        candidate_groups = payload.get("candidate_groups")
        selected_assets = payload.get("selected_assets")
        candidate_groups = (
            candidate_groups if isinstance(candidate_groups, Mapping) else {}
        )
        selected_assets = (
            selected_assets if isinstance(selected_assets, Mapping) else {}
        )
        for group_name in ("metrics", "dimensions", "values", "terms"):
            values = candidate_groups.get(group_name)
            if isinstance(values, list):
                merged.setdefault("candidate_groups", {}).setdefault(
                    group_name, []
                ).extend(values)
            values = selected_assets.get(group_name)
            if isinstance(values, list):
                merged.setdefault("selected_assets", {}).setdefault(
                    group_name, []
                ).extend(values)
    if contexts:
        combined = SemanticContext()
        for item in contexts:
            combined = _merge_semantic_context(combined, item)
        merged["semantic_context"] = combined
    return merged


def _semantic_context_from_search_payload(
    context: ResearchToolContext,
    payload: Any,
    args: SearchSemanticAssetsArguments,
) -> SemanticContext:
    """把检索结果投影为有界 SemanticContext，并再次执行 Scope 过滤。"""

    if isinstance(payload, SemanticContext):
        source = payload
    elif isinstance(payload, Mapping):
        raw_context = payload.get("semantic_context")
        if isinstance(raw_context, SemanticContext):
            source = raw_context
        elif isinstance(raw_context, Mapping):
            source = SemanticContext.model_validate(raw_context)
        else:
            source = _context_from_candidate_payload(context, payload, args)
    else:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.RESULT_INVALID,
            "补充语义检索结果不是对象",
        )
    filtered = _filter_semantic_context(context, source, args)
    return filtered


def _context_from_candidate_payload(
    context: ResearchToolContext,
    payload: Mapping[str, Any],
    args: SearchSemanticAssetsArguments,
) -> SemanticContext:
    groups = payload.get("candidate_groups") or payload.get("selected_assets") or {}
    if not isinstance(groups, Mapping):
        groups = {}
    metrics = tuple(
        metric
        for raw in _limited_items(groups.get("metrics"), args.limit)
        if isinstance(raw, Mapping)
        for metric in [_semantic_metric_from_item(raw)]
        if metric is not None
    )
    dimensions = tuple(
        dimension
        for raw in _limited_items(groups.get("dimensions"), args.limit)
        if isinstance(raw, Mapping)
        for dimension in [_semantic_dimension_from_item(raw)]
        if dimension is not None
    )
    schema = _frozen_schema(context)
    schema_context = _semantic_context_from_schema(context, schema, args)
    return SemanticContext(
        metrics=metrics or schema_context.metrics,
        dimensions=dimensions or schema_context.dimensions,
        hierarchies=schema_context.hierarchies,
        metric_formulas=schema_context.metric_formulas,
        metric_analysis_relations=schema_context.metric_analysis_relations,
        ambiguities=schema_context.ambiguities,
    )


def _filter_semantic_context(
    context: ResearchToolContext,
    source: SemanticContext,
    args: SearchSemanticAssetsArguments,
) -> SemanticContext:
    allowed = _allowed_semantic_refs(context)
    requested = set(args.asset_types)
    metrics = tuple(
        item.model_copy(
            update={
                "dimensions": tuple(
                    ref for ref in item.dimensions if ref in allowed
                )
            }
        )
        for item in source.metrics
        if "metric" in requested and item.ref in allowed
    )[: args.limit]
    dimensions = tuple(
        item for item in source.dimensions
        if "dimension" in requested and item.ref in allowed
    )[: args.limit]
    hierarchies = tuple(
        item
        for item in source.hierarchies
        if "hierarchy" in requested
        and all(ref in allowed for ref in item.levels)
    )[: args.limit]
    formulas = tuple(
        item
        for item in source.metric_formulas
        if "metric_formula" in requested
        and item.target_metric_ref in allowed
        and all(ref in allowed for ref in item.source_metric_refs)
    )[: args.limit]
    relations = tuple(
        item
        for item in source.metric_analysis_relations
        if "metric_analysis_relation" in requested
        and item.metric_ref in allowed
        and all(ref in allowed for ref in item.related_metric_refs)
    )[: args.limit]
    ambiguities = tuple(
        item
        for item in source.ambiguities
        if all(ref in allowed for ref in item.candidate_refs)
    )[: args.limit]
    return SemanticContext(
        metrics=metrics,
        dimensions=dimensions,
        hierarchies=hierarchies,
        metric_formulas=formulas,
        metric_analysis_relations=relations,
        ambiguities=ambiguities,
    )


def _merge_semantic_context(
    current: SemanticContext,
    added: SemanticContext,
) -> SemanticContext:
    """按正式引用合并语义资产；已有资产优先，保证重放结果稳定。"""

    def merge(
        first: tuple[Any, ...],
        second: tuple[Any, ...],
        key: Any,
    ) -> tuple[Any, ...]:
        values: dict[Any, Any] = {key(item): item for item in first}
        values.update({key(item): item for item in second if key(item) not in values})
        return tuple(values.values())

    return SemanticContext(
        metrics=merge(current.metrics, added.metrics, lambda item: item.ref),
        dimensions=merge(current.dimensions, added.dimensions, lambda item: item.ref),
        hierarchies=merge(current.hierarchies, added.hierarchies, lambda item: item.ref),
        metric_formulas=merge(
            current.metric_formulas,
            added.metric_formulas,
            lambda item: item.target_metric_ref,
        ),
        metric_analysis_relations=merge(
            current.metric_analysis_relations,
            added.metric_analysis_relations,
            lambda item: item.metric_ref,
        ),
        ambiguities=merge(current.ambiguities, added.ambiguities, lambda item: item.term),
    )


def _semantic_context_difference(
    current: SemanticContext,
    candidate: SemanticContext,
) -> SemanticContext:
    """只保留当前输入快照中尚不存在的正式语义资产。"""

    def difference(
        existing: tuple[Any, ...],
        values: tuple[Any, ...],
        key: Any,
    ) -> tuple[Any, ...]:
        existing_keys = {key(item) for item in existing}
        return tuple(item for item in values if key(item) not in existing_keys)

    return SemanticContext(
        metrics=difference(current.metrics, candidate.metrics, lambda item: item.ref),
        dimensions=difference(
            current.dimensions,
            candidate.dimensions,
            lambda item: item.ref,
        ),
        hierarchies=difference(
            current.hierarchies,
            candidate.hierarchies,
            lambda item: item.ref,
        ),
        metric_formulas=difference(
            current.metric_formulas,
            candidate.metric_formulas,
            lambda item: item.target_metric_ref,
        ),
        metric_analysis_relations=difference(
            current.metric_analysis_relations,
            candidate.metric_analysis_relations,
            lambda item: item.metric_ref,
        ),
        ambiguities=difference(
            current.ambiguities,
            candidate.ambiguities,
            lambda item: item.term,
        ),
    )


def _frozen_schema(context: ResearchToolContext) -> Any:
    scope = context.context.semantic_asset_scope
    return scope.schema_snapshot if scope is not None else None


def _semantic_context_from_schema(
    context: ResearchToolContext,
    schema: Any,
    args: SearchSemanticAssetsArguments,
) -> SemanticContext:
    if schema is None:
        return SemanticContext()
    allowed = _allowed_semantic_refs(context)
    requested = set(args.asset_types)
    metrics = tuple(
        item
        for raw in getattr(schema, "metrics", ())
        for item in [_semantic_metric_from_schema_element(raw)]
        if item is not None and item.ref in allowed and "metric" in requested
    )[: args.limit]
    dimensions = tuple(
        item
        for raw in getattr(schema, "dimensions", ())
        for item in [_semantic_dimension_from_schema_element(raw)]
        if item is not None and item.ref in allowed and "dimension" in requested
    )[: args.limit]
    hierarchies: tuple[SemanticHierarchy, ...] = ()
    if "hierarchy" in requested:
        hierarchy_items: list[SemanticHierarchy] = []
        for raw in getattr(schema, "dimension_hierarchies", ()):
            levels = tuple(
                ref
                for refs in _field(raw, "dimension_refs_by_model", {}).values()
                for ref in refs
                if ref in allowed
            )
            if len(levels) < 2:
                continue
            hierarchy_items.append(
                SemanticHierarchy(
                    ref=f"HIERARCHY:{_field(raw, 'id', 'unknown')}:{context.dataset_id}",
                    name=str(_field(raw, "id", "hierarchy")),
                    levels=tuple(dict.fromkeys(levels)),
                )
            )
        hierarchies = tuple(hierarchy_items[: args.limit])
    relations = _schema_relations(context, schema, args)
    formulas = _schema_formulas(context, schema, args)
    return SemanticContext(
        metrics=metrics,
        dimensions=dimensions,
        hierarchies=hierarchies,
        metric_formulas=formulas,
        metric_analysis_relations=relations,
    )


def _schema_relations(
    context: ResearchToolContext,
    schema: Any,
    args: SearchSemanticAssetsArguments,
) -> tuple[Any, ...]:
    if "metric_analysis_relation" not in set(args.asset_types):
        return ()
    allowed = _allowed_semantic_refs(context)
    relations: list[Any] = []
    for raw in getattr(schema, "research_relationships", ()):
        target = _field(raw, "target_metric_ref")
        related = tuple(
            dict.fromkeys(
                (
                    _field(raw, "driver_metric_ref"),
                    *_field(raw, "component_metric_refs", ()),
                )
            )
        )
        if (
            isinstance(target, str)
            and target in allowed
            and related
            and all(isinstance(ref, str) and ref in allowed for ref in related)
        ):
            relations.append(
                # 同一指标允许多个关系类型时，DTO 目前按 metric_ref 去重；
                # 使用治理关系类型作为稳定的分析类型摘要。
                MetricAnalysisRelation(
                    metric_ref=target,
                    related_metric_refs=related,
                    analysis_type=str(_field(raw, "relationship_type", "driver")),
                )
            )
        if len(relations) >= args.limit:
            break
    return tuple(relations)


def _schema_formulas(
    context: ResearchToolContext,
    schema: Any,
    args: SearchSemanticAssetsArguments,
) -> tuple[Any, ...]:
    if "metric_formula" not in set(args.asset_types):
        return ()
    allowed = _allowed_semantic_refs(context)
    by_id = {
        getattr(item, "id", None): _schema_element_ref("METRIC", item)
        for item in getattr(schema, "metrics", ())
    }
    formulas: list[Any] = []
    for raw in getattr(schema, "metrics", ()):
        target = _schema_element_ref("METRIC", raw)
        definition = _field(_field(raw, "type_params", {}), "formula_definition")
        if not isinstance(definition, Mapping):
            definition = _field(_field(raw, "ext_info", {}), "formula_definition")
        if target not in allowed or not isinstance(definition, Mapping):
            continue
        refs = tuple(
            ref
            for component in definition.get("components") or ()
            if isinstance(component, Mapping)
            for ref in [by_id.get(component.get("metric_id"))]
            if isinstance(ref, str)
        )
        if len(refs) < 1 or any(ref not in allowed for ref in refs):
            continue
        operation = str(definition.get("operation") or "").upper()
        separator = {"RATIO": " / ", "DIFFERENCE": " - ", "PRODUCT": " * ", "SUM": " + "}.get(operation, " + ")
        formulas.append(
            MetricFormula(
                target_metric_ref=target,
                expression=separator.join(refs),
                source_metric_refs=refs,
            )
        )
        if len(formulas) >= args.limit:
            break
    return tuple(formulas)


def _semantic_metric_from_item(item: Mapping[str, Any]) -> SemanticMetric | None:
    ref = item.get("ref")
    if not isinstance(ref, str):
        ref = _candidate_ref("METRIC", item)
    if not isinstance(ref, str):
        return None
    dimensions = tuple(
        value
        for value in item.get("dimensions") or item.get("dimension_refs") or ()
        if isinstance(value, str)
    )
    return SemanticMetric(
        ref=ref,
        name=str(item.get("name") or item.get("display_name") or item.get("biz_name") or ref),
        description=str(item.get("description") or ""),
        aggregation=str(item.get("aggregation") or item.get("default_agg") or "SUM"),
        unit=item.get("unit") if isinstance(item.get("unit"), str) else None,
        dimensions=dimensions,
    )


def _semantic_dimension_from_item(item: Mapping[str, Any]) -> SemanticDimension | None:
    ref = item.get("ref")
    if not isinstance(ref, str):
        ref = _candidate_ref("DIMENSION", item)
    if not isinstance(ref, str):
        return None
    grains = tuple(value for value in item.get("grains") or () if isinstance(value, str))
    return SemanticDimension(
        ref=ref,
        name=str(item.get("name") or item.get("display_name") or item.get("biz_name") or ref),
        description=str(item.get("description") or ""),
        grains=grains,
    )


def _semantic_metric_from_schema_element(item: Any) -> SemanticMetric | None:
    ref = _schema_element_ref("METRIC", item)
    if ref is None:
        return None
    type_params = _field(item, "type_params", {})
    unit = type_params.get("unit") if isinstance(type_params, Mapping) else None
    return SemanticMetric(
        ref=ref,
        name=str(_field(item, "name", _field(item, "biz_name", ref))),
        description=str(_field(item, "description", "") or ""),
        aggregation=str(_field(item, "default_agg", "SUM") or "SUM"),
        unit=unit if isinstance(unit, str) else None,
    )


def _semantic_dimension_from_schema_element(item: Any) -> SemanticDimension | None:
    ref = _schema_element_ref("DIMENSION", item)
    if ref is None:
        return None
    return SemanticDimension(
        ref=ref,
        name=str(_field(item, "name", _field(item, "biz_name", ref))),
        description=str(_field(item, "description", "") or ""),
    )


def _schema_element_ref(kind: str, item: Any) -> str | None:
    asset_id = _field(item, "id")
    model_id = _field(item, "model")
    if not isinstance(asset_id, int) or asset_id <= 0:
        return None
    if not isinstance(model_id, int) or model_id <= 0:
        return None
    return f"{kind}:{asset_id}:{model_id}"


def _candidate_ref(kind: str, item: Mapping[str, Any]) -> str | None:
    asset_id = item.get("asset_id")
    model_id = item.get("model_id")
    if isinstance(asset_id, int) and asset_id > 0 and isinstance(model_id, int) and model_id > 0:
        return f"{kind}:{asset_id}:{model_id}"
    return None


def _limited_items(value: Any, limit: int) -> tuple[Any, ...]:
    return tuple(value[:limit]) if isinstance(value, list) else ()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _validate_finish_completion(
    context: ResearchToolContext,
    completion: Completion,
) -> list[CompletionValidationError]:
    """执行 finish_research 的全部确定性引用校验。"""

    errors: list[CompletionValidationError] = []
    if context.current_status is not ResearchStateStatus.RUNNING:
        errors.append(
            _completion_error(
                "RESEARCH_AGENT_RUN_NOT_RUNNING",
                "Research Run 当前不处于 running 状态",
            )
        )
    if context.react_completion() is not None:
        errors.append(
            _completion_error(
                "RESEARCH_AGENT_COMPLETION_ALREADY_ACCEPTED",
                "Research Run 已经存在通过校验的 Completion",
            )
        )

    evidences = {
        evidence_id: context.research_evidence(evidence_id)
        for evidence_id in completion.evidence_ids
    }
    produced_by_tool = _successful_react_evidence_ids(context)
    for evidence_id, evidence in evidences.items():
        if evidence is None:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_EVIDENCE_NOT_FOUND",
                    f"Evidence {evidence_id} 不属于当前 Run",
                    evidence_id=evidence_id,
                )
            )
        elif evidence_id not in produced_by_tool:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_EVIDENCE_TOOL_RESULT_REQUIRED",
                    f"Evidence {evidence_id} 没有来自 query 或 compute ToolResult",
                    evidence_id=evidence_id,
                )
            )

    findings = {item.finding_id: item for item in context.react_findings()}
    allowed_refs = _allowed_semantic_refs(context)
    for finding_id in completion.finding_ids:
        finding = findings.get(finding_id)
        if finding is None:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_FINDING_NOT_FOUND",
                    f"Finding {finding_id} 不属于当前 Run",
                    finding_id=finding_id,
                )
            )
            continue
        if finding.status != "confirmed":
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_FINDING_NOT_CONFIRMED",
                    f"Finding {finding_id} 不是 confirmed 状态",
                    finding_id=finding_id,
                )
            )
        missing_evidence = set(finding.evidence_ids) - set(completion.evidence_ids)
        if missing_evidence:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_FINDING_EVIDENCE_NOT_INCLUDED",
                    f"Finding {finding_id} 的证据未全部列入 Completion",
                    finding_id=finding_id,
                )
            )
        for ref in (
            *finding.scope.metric_refs,
            *finding.scope.dimension_refs,
            *(item.field_ref for item in finding.scope.filters),
        ):
            if ref not in allowed_refs:
                errors.append(
                    _completion_error(
                        "RESEARCH_AGENT_COMPLETION_SCOPE_OUT_OF_SCOPE",
                        f"Finding {finding_id} 的 Scope 引用 {ref} 超出冻结范围",
                        finding_id=finding_id,
                    )
                )
        for evidence_id in finding.evidence_ids:
            evidence = context.research_evidence(evidence_id)
            if evidence is not None:
                errors.extend(_validate_finding_scope(finding, evidence))

    attempts = {item.attempt_id for item in context.react_attempts()}
    for limitation in completion.limitations:
        for attempt_id in limitation.attempt_ids:
            if attempt_id not in attempts:
                errors.append(
                    _completion_error(
                        "RESEARCH_AGENT_COMPLETION_ATTEMPT_NOT_FOUND",
                        f"限制引用的 Attempt {attempt_id} 不存在",
                    )
                )
    for tool_call_id in context.running_tool_call_ids():
        errors.append(
            _completion_error(
                "RESEARCH_AGENT_COMPLETION_TOOL_CALL_RUNNING",
                f"工具调用 {tool_call_id} 尚未结束",
            )
        )
    return errors


def _successful_react_evidence_ids(context: ResearchToolContext) -> set[str]:
    evidence_ids: set[str] = set()
    for raw in context.research_tool_results():
        result = raw.get("tool_result") if isinstance(raw.get("tool_result"), dict) else raw
        if not isinstance(result, Mapping):
            continue
        if result.get("status") != "succeeded":
            continue
        if result.get("name") not in {
            ResearchActionType.QUERY_SEMANTIC_DATA.value,
            ResearchActionType.COMPUTE_EVIDENCE.value,
        }:
            continue
        payload = result.get("result")
        if isinstance(payload, Mapping) and isinstance(payload.get("evidence_id"), str):
            evidence_ids.add(payload["evidence_id"])
    return evidence_ids


def _validate_finding_scope(
    finding: Finding,
    evidence: Evidence,
) -> list[CompletionValidationError]:
    available_refs = {
        *evidence.definition.metrics,
        *evidence.definition.dimensions,
        *(
            column.semantic_ref
            for column in evidence.columns
            if column.semantic_ref is not None
        ),
    }
    errors: list[CompletionValidationError] = []
    for ref in (*finding.scope.metric_refs, *finding.scope.dimension_refs):
        if ref not in available_refs:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_SCOPE_NOT_SUPPORTED",
                    f"Evidence {evidence.evidence_id} 无法定位 Finding Scope 引用 {ref}",
                    finding_id=finding.finding_id,
                    evidence_id=evidence.evidence_id,
                )
            )
    if finding.scope.time_ranges and not any(
        time_range in evidence.definition.time_ranges
        for time_range in finding.scope.time_ranges
    ):
        errors.append(
            _completion_error(
                "RESEARCH_AGENT_COMPLETION_TIME_SCOPE_NOT_SUPPORTED",
                f"Evidence {evidence.evidence_id} 无法定位 Finding 的时间范围",
                finding_id=finding.finding_id,
                evidence_id=evidence.evidence_id,
            )
        )
    filter_refs = {item.field_ref for item in evidence.definition.filters}
    filter_refs.update(available_refs)
    for item in finding.scope.filters:
        if item.field_ref not in filter_refs:
            errors.append(
                _completion_error(
                    "RESEARCH_AGENT_COMPLETION_FILTER_SCOPE_NOT_SUPPORTED",
                    f"Evidence {evidence.evidence_id} 无法定位 Finding 的筛选字段 {item.field_ref}",
                    finding_id=finding.finding_id,
                    evidence_id=evidence.evidence_id,
                )
            )
    return errors


def _completion_error(
    code: str,
    message: str,
    *,
    finding_id: str | None = None,
    evidence_id: str | None = None,
) -> CompletionValidationError:
    return CompletionValidationError(
        code=code,
        message=message,
        finding_id=finding_id,
        evidence_id=evidence_id,
    )


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


def _evidence_from_semantic_result(
    legacy: ResearchEvidence,
    *,
    evidence_id: str,
    purpose: str,
    definition: EvidenceDefinition,
) -> Evidence:
    columns = tuple(_column_from_semantic(item) for item in legacy.logical_columns)
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
    columns = tuple(_column_from_semantic(item) for item in logical_columns)
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


def _semantic_logical_field(
    evidence: ResearchEvidence,
    ref: str,
    role: str | None = None,
) -> str | None:
    """按语义列映射读取结果字段，不按列位置推断。"""

    fallback: str | None = None
    for column in evidence.logical_columns:
        if column.asset_ref != ref or not column.result_field:
            continue
        if role is not None and column.value_role == role:
            return column.result_field
        if fallback is None:
            fallback = column.result_field
    return fallback


def _load_compute_snapshot(
    context: ResearchToolContext,
    evidence: ResearchEvidence,
) -> ResultSetSnapshot:
    """从 ResultStore 读取计算输入的完整结果。"""

    if context.result_store is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            "ResultStore 未配置",
            retryable=True,
            same_parameter_retryable=True,
        )
    payload = context.result_set_payload(evidence.result_ref.result_id)
    if payload is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PERSISTENCE_FAILED,
            f"结果集 {evidence.result_ref.result_id} 不存在或已清理",
            retryable=True,
            same_parameter_retryable=True,
        )
    try:
        ref = ResultSetRef.model_validate(payload)
        execution_id, chat_id, record_id, _ = context.execution_identity()
        return context.result_store.read(
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


def _compute_failure(
    code: str,
    message: str,
    *,
    parameter_retryable: bool = True,
    same_parameter_retryable: bool = False,
) -> ResearchToolExecutionError:
    """把计算参数或引擎错误转换为 Research 工具错误。"""

    return ResearchToolExecutionError(
        code,
        message,
        retryable=True,
        parameter_retryable=parameter_retryable,
        same_parameter_retryable=same_parameter_retryable,
    )


def _build_compute_task(
    context: ResearchToolContext,
    request: ResearchComputeRequest,
    inputs: tuple[ResearchEvidence, ...],
) -> ComputeTask | None:
    """把白名单计算操作转换为 ComputeEngine 任务。"""

    operation = request.operation
    if operation is ResearchComputeOperation.RANKING:
        return None
    if operation in {
        ResearchComputeOperation.DIFFERENCE,
        ResearchComputeOperation.GROWTH_RATE,
    }:
        if len(inputs) != 2:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "差值或增长率需要恰好 2 个输入 Evidence（当前期在前、对照期在后）",
            )
        keys = _compute_join_keys(context, request, inputs[0], inputs[1])
        return ComputeTask(
            id=f"{operation.value}-output",
            operation=(
                ComputeOperation.DIFFERENCE
                if operation is ResearchComputeOperation.DIFFERENCE
                else ComputeOperation.GROWTH_RATE
            ),
            inputs=request.input_evidence_ids,
            join_on=keys,
        )
    if operation is ResearchComputeOperation.SHARE:
        evidence = _single_compute_input(request, inputs)
        metric = _compute_metric_field(request, evidence)
        dimensions = _compute_dimension_fields(request, evidence)
        options: dict[str, Any] = {"metrics": [metric]}
        if dimensions:
            options["dimensions"] = dimensions
        return ComputeTask(
            id="share-output",
            operation=ComputeOperation.SHARE,
            inputs=(request.input_evidence_ids[0],),
            options=options,
        )
    if operation is ResearchComputeOperation.RATIO:
        evidence = _single_compute_input(request, inputs)
        if len(request.metric_refs) != 2:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "ratio 需要恰好 2 个指标（分子、分母）",
            )
        numerator = _semantic_logical_field(evidence, request.metric_refs[0], "value")
        denominator = _semantic_logical_field(evidence, request.metric_refs[1], "value")
        if numerator is None or denominator is None:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "ratio 的指标无法映射到 Evidence 字段",
            )
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
        )
    if operation is ResearchComputeOperation.TOPN_OTHER:
        evidence = _single_compute_input(request, inputs)
        dimension_fields = _compute_dimension_fields(request, evidence)
        if len(dimension_fields) != 1:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "topn_other 需要恰好 1 个维度",
            )
        metric = _compute_metric_field(request, evidence)
        if request.limit is None:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "topn_other 必须提供 limit",
            )
        return ComputeTask(
            id="topn-output",
            operation=ComputeOperation.TOPN_OTHER,
            inputs=(request.input_evidence_ids[0],),
            options={
                "dimension": dimension_fields[0],
                "metrics": [metric],
                "top_n": request.limit,
            },
        )
    if operation is ResearchComputeOperation.MERGE:
        if len(inputs) < 2:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "merge 需要至少 2 个输入 Evidence",
            )
        return ComputeTask(
            id="merge-output",
            operation=ComputeOperation.MERGE,
            inputs=request.input_evidence_ids,
            join_on=_compute_join_keys(context, request, inputs[0], inputs[1]),
        )
    if operation in {
        ResearchComputeOperation.CONTRIBUTION,
        ResearchComputeOperation.RECONCILIATION,
    }:
        return _build_contribution_task(context, request, inputs)
    raise ResearchToolExecutionError(
        ResearchToolExecutionError.UNSUPPORTED_CAPABILITY,
        f"操作 {operation.value} 暂不支持",
    )


def _single_compute_input(
    request: ResearchComputeRequest,
    inputs: tuple[ResearchEvidence, ...],
) -> ResearchEvidence:
    if len(inputs) != 1:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"{request.operation.value} 需要恰好 1 个输入 Evidence",
        )
    return inputs[0]


def _compute_join_keys(
    _context: ResearchToolContext,
    request: ResearchComputeRequest,
    left: ResearchEvidence,
    right: ResearchEvidence,
) -> tuple[str, ...]:
    refs = request.group_by_refs or request.dimension_refs
    keys: list[str] = []
    for ref in refs:
        left_field = _semantic_logical_field(left, ref, "group_key")
        right_field = _semantic_logical_field(right, ref, "group_key")
        if left_field is None or right_field is None:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"维度 {ref} 无法在两个输入 Evidence 上对齐",
            )
        keys.append(left_field)
    if not keys:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            "至少提供一个分组维度用于输入对齐，避免笛卡尔积",
        )
    return tuple(keys)


def _compute_metric_field(
    request: ResearchComputeRequest,
    evidence: ResearchEvidence,
) -> str:
    if len(request.metric_refs) != 1:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"{request.operation.value} 需要恰好 1 个指标",
        )
    field = _semantic_logical_field(evidence, request.metric_refs[0], "value")
    if field is None:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"指标 {request.metric_refs[0]} 无法映射到 Evidence 字段",
        )
    return field


def _compute_dimension_fields(
    request: ResearchComputeRequest,
    evidence: ResearchEvidence,
) -> list[str]:
    fields: list[str] = []
    for ref in (*request.dimension_refs, *request.group_by_refs):
        field = _semantic_logical_field(evidence, ref, "group_key")
        if field is None:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"维度 {ref} 无法映射到 Evidence 字段",
            )
        fields.append(field)
    return fields


def _build_contribution_task(
    _context: ResearchToolContext,
    request: ResearchComputeRequest,
    inputs: tuple[ResearchEvidence, ...],
) -> ComputeTask:
    if len(inputs) != 2:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            "contribution 或 reconciliation 需要恰好 2 个输入 Evidence（分解在前、总量在后）",
        )
    if len(request.metric_refs) != 1:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            "contribution 或 reconciliation 需要恰好 1 个指标",
        )
    breakdown, total = inputs
    metric_ref = request.metric_refs[0]
    difference_column = _semantic_logical_field(breakdown, metric_ref, "difference")
    total_difference_column = _semantic_logical_field(total, metric_ref, "difference")
    if difference_column is None:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"分解 Evidence 缺少指标 {metric_ref} 的差值列，请先执行对比查询",
        )
    if total_difference_column is None:
        raise _compute_failure(
            ResearchToolExecutionError.ARGUMENTS_INVALID,
            f"总量 Evidence 缺少指标 {metric_ref} 的差值列",
        )
    dimensions = _compute_dimension_fields(request, breakdown)
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
        inputs=request.input_evidence_ids,
        options=options,
    )


def _execute_compute_plan(
    context: ResearchToolContext,
    request: ResearchComputeRequest,
    inputs: tuple[ResearchEvidence, ...],
    task: ComputeTask | None,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    if task is None:
        evidence = inputs[0]
        snapshot = _load_compute_snapshot(context, evidence)
        if not request.order:
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                "ranking 需要至少一个排序引用",
            )
        rows = [dict(row) for row in snapshot.rows]
        for item in reversed(request.order):
            field = _semantic_logical_field(evidence, item.ref, item.value_role)
            if field is None:
                raise _compute_failure(
                    ResearchToolExecutionError.ARGUMENTS_INVALID,
                    f"排序引用 {item.ref} 无法映射到 Evidence 字段",
                )
            rows = _safe_row_sort(
                rows,
                field,
                item.direction is ResearchOrderDirection.DESC,
            )
        limit = min(request.limit or 100, 1_000)
        return rows[:limit], [str(field) for field in snapshot.ref.fields], None
    if context.compute_engine is None:
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.PREPARE_FAILED,
            "计算引擎未配置",
        )
    snapshots = {
        evidence_id: _load_compute_snapshot(context, evidence)
        for evidence_id, evidence in zip(
            request.input_evidence_ids, inputs, strict=True
        )
    }
    try:
        computed = context.compute_engine.execute(task, snapshots)
    except ComputeEngineError as exc:
        if exc.code == "COMPUTE_CONTRIBUTION_RECONCILIATION_FAILED":
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.RECONCILIATION_FAILED,
                "贡献分解与总差值对账失败，分组数据与总量不一致",
                retryable=True,
                parameter_retryable=True,
            ) from exc
        if exc.code == "COMPUTE_SQL_EXECUTION_FAILED":
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.EXECUTION_FAILED,
                "计算引擎执行失败",
                retryable=True,
                same_parameter_retryable=True,
            ) from exc
        if isinstance(exc, ComputeOperationError) or exc.code.startswith("COMPUTE_EXPR"):
            raise _compute_failure(
                ResearchToolExecutionError.ARGUMENTS_INVALID,
                f"计算参数不合法：{exc.code}",
            ) from exc
        raise ResearchToolExecutionError(
            ResearchToolExecutionError.EXECUTION_FAILED,
            f"计算失败：{exc.code}",
            retryable=True,
            same_parameter_retryable=True,
        ) from exc
    return [dict(row) for row in computed.rows], list(computed.fields), computed.sql


def _safe_row_sort(
    rows: list[dict[str, Any]],
    field: str,
    descending: bool,
) -> list[dict[str, Any]]:
    """确定性排序：NULL 排在最后，混合类型按字符串排序。"""

    try:
        return sorted(rows, key=lambda row: (row.get(field) is None, row.get(field)), reverse=descending)
    except TypeError:
        return sorted(
            rows,
            key=lambda row: (row.get(field) is None, str(row.get(field))),
            reverse=descending,
        )


def _derived_compute_columns(
    request: ResearchComputeRequest,
    inputs: tuple[ResearchEvidence, ...],
    fields: list[str],
) -> tuple[ResearchLogicalColumn, ...]:
    """继承输入逻辑列，并为计算输出列补充受控映射。"""

    inherited: dict[str, ResearchLogicalColumn] = {}
    for evidence in inputs:
        for logical_column in evidence.logical_columns:
            if logical_column.result_field and logical_column.result_field not in inherited:
                inherited[logical_column.result_field] = logical_column
    primary_metric = next(iter(request.metric_refs), None)
    primary_dimension = next(
        (ref for ref in (*request.dimension_refs, *request.group_by_refs)),
        None,
    )
    result: list[ResearchLogicalColumn] = []
    seen: set[tuple[str, str, str]] = set()
    for field in fields:
        column: ResearchLogicalColumn | None = inherited.get(field)
        if column is None:
            column = _synthetic_compute_column(
                field,
                primary_metric,
                primary_dimension,
            )
        if column is None:
            continue
        key = (column.asset_ref, column.value_role, field)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ResearchLogicalColumn(
                asset_ref=column.asset_ref,
                value_role=column.value_role,
                result_field=field,
            )
        )
    return tuple(result)


def _synthetic_compute_column(
    field: str,
    metric_ref: str | None,
    dimension_ref: str | None,
) -> ResearchLogicalColumn | None:
    def build(asset_ref: str | None, role: str) -> ResearchLogicalColumn | None:
        if asset_ref is None:
            return None
        return ResearchLogicalColumn(
            asset_ref=asset_ref,
            value_role=cast(Any, role),
            result_field=field,
        )

    if field == "dimension_value":
        return build(dimension_ref, "group_key")
    if field == "metric_value":
        return build(metric_ref, "value")
    for suffix, role in (
        ("_share", "share"),
        ("_growth_rate", "growth_rate"),
        ("_difference", "difference"),
        ("_current", "current"),
        ("_previous", "previous"),
        ("_contribution", "contribution"),
    ):
        if field.endswith(suffix):
            return build(metric_ref, role)
    if field in {"total_difference", "reconciliation_difference"}:
        return build(metric_ref, "difference")
    return build(metric_ref, "value")


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


def _column_from_semantic(column: ResearchLogicalColumn) -> EvidenceColumn:
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
