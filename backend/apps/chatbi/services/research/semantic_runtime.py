"""Research Semantic Query 的受治理运行时。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Collection, Generator
from dataclasses import dataclass
from threading import RLock
from typing import Any

from apps.chatbi.errors import (
    ResultArtifactReadError,
    ResultArtifactWriteError,
    SemanticQueryBoundaryError,
    SemanticQueryBuildError,
    SemanticQueryProjectionError,
    SemanticQueryRuntimeError,
)
from apps.chatbi.models import ChatbiAgentRun
from apps.chatbi.models.dto.analysis_plan import ResultSetRef
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    SemanticOperation,
)
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchEvidence,
    ResearchEvidenceStatistics,
    ResearchLogicalColumn,
    ResearchQueryComparison,
    ResearchResultRef,
    ResearchSemanticQuery,
    ResearchSemanticQueryOutcome,
    ResearchToolCallRef,
    ToolErrorCode,
    ToolFailureStage,
)
from apps.chatbi.services.execution.analysis_execution import (
    AnalysisExecutionService,
    PlanPipelineError,
)
from apps.chatbi.services.planning.execution_state import PLAN_EXECUTION_STATE_KEY
from apps.chatbi.services.research.semantic_query_builder import SemanticQueryBuilder
from apps.conversation import ChatRecordExecutionType
from apps.semantic.errors import SemanticForbiddenError, SemanticValidationError
from apps.tool import NeverCancelled
from apps.tool.tools.semantic_contracts import SemanticAssetScope

_BOUNDARY_ERROR_CODES = {
    "UNSUPPORTED_CAPABILITY": ToolErrorCode.UNSUPPORTED_CAPABILITY,
    "RESEARCH_AGENT_REQUIREMENT_REQUIRED": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_QUERY_SCOPE_MISMATCH": ToolErrorCode.SCOPE_DENIED,
    "RESEARCH_AGENT_QUERY_METRIC_OUT_OF_SCOPE": ToolErrorCode.SCOPE_DENIED,
    "RESEARCH_AGENT_QUERY_DIMENSION_OUT_OF_SCOPE": ToolErrorCode.SCOPE_DENIED,
    "RESEARCH_AGENT_QUERY_FILTER_OUT_OF_SCOPE": ToolErrorCode.SCOPE_DENIED,
    "RESEARCH_AGENT_IMMUTABLE_FILTER_MISSING": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_IMMUTABLE_FILTER_CHANGED": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_EVIDENCE_CROSS_RUN": ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
    "RESEARCH_AGENT_EVIDENCE_NOT_FOUND": ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
    "RESEARCH_AGENT_EVIDENCE_COLUMN_NOT_FOUND": ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
    "RESEARCH_AGENT_QUERY_TIME_BINDING_CHANGED": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_QUERY_VERSION_MISMATCH": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_QUERY_RUN_MISMATCH": ToolErrorCode.INVALID_REQUEST,
    "RESEARCH_AGENT_CONTEXT_RUN_MISMATCH": ToolErrorCode.PERMISSION_DENIED,
    "DATASET_SCOPE_MISMATCH": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_SCHEMA_VERSION_CHANGED": ToolErrorCode.INVALID_REQUEST,
    "SEMANTIC_SCHEMA_FINGERPRINT_CHANGED": ToolErrorCode.INVALID_REQUEST,
    "SEMANTIC_CONTRACT_VERSION_CHANGED": ToolErrorCode.INVALID_REQUEST,
    "SEMANTIC_PERMISSION_VERSION_CHANGED": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_PERMISSION_FINGERPRINT_CHANGED": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_SCHEMA_DATASET_MISMATCH": ToolErrorCode.PERMISSION_DENIED,
    "RESULT_STORE_FAILED": ToolErrorCode.RESULT_STORE_FAILED,
    "RESEARCH_AGENT_EXECUTION_OUTCOME_INVALID": ToolErrorCode.EXECUTION_FAILED,
    "RESEARCH_AGENT_PRIMARY_RESULT_MISSING": ToolErrorCode.EXECUTION_FAILED,
    "SEMANTIC_SCOPE_REQUIRED": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_SCOPE_FINGERPRINT_REQUIRED": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_PERMISSION_FINGERPRINT_REQUIRED": ToolErrorCode.PERMISSION_DENIED,
    "SEMANTIC_PERMISSION_VERSION_REQUIRED": ToolErrorCode.PERMISSION_DENIED,
    "RESEARCH_AGENT_CONTEXT_RUN_REQUIRED": ToolErrorCode.PERMISSION_DENIED,
    "SCOPE_DENIED": ToolErrorCode.SCOPE_DENIED,
    "EVIDENCE_REFERENCE_INVALID": ToolErrorCode.EVIDENCE_REFERENCE_INVALID,
}
_CONTRACT_VALIDATION_CODES = frozenset(
    {
        "RESEARCH_AGENT_REQUIREMENT_REQUIRED",
        "RESEARCH_AGENT_QUERY_SCOPE_MISMATCH",
        "RESEARCH_AGENT_QUERY_METRIC_OUT_OF_SCOPE",
        "RESEARCH_AGENT_QUERY_DIMENSION_OUT_OF_SCOPE",
        "RESEARCH_AGENT_QUERY_FILTER_OUT_OF_SCOPE",
        "RESEARCH_AGENT_IMMUTABLE_FILTER_MISSING",
        "RESEARCH_AGENT_IMMUTABLE_FILTER_CHANGED",
        "RESEARCH_AGENT_EVIDENCE_CROSS_RUN",
        "RESEARCH_AGENT_EVIDENCE_NOT_FOUND",
        "RESEARCH_AGENT_EVIDENCE_COLUMN_NOT_FOUND",
        "RESEARCH_AGENT_QUERY_TIME_BINDING_CHANGED",
        "RESEARCH_AGENT_QUERY_VERSION_MISMATCH",
        "RESEARCH_AGENT_QUERY_RUN_MISMATCH",
        "RESEARCH_AGENT_SCOPE_FINGERPRINT_MISMATCH",
        "RESEARCH_AGENT_CONTRIBUTION_METRIC_OUT_OF_SCOPE",
        "RESEARCH_AGENT_CONTRIBUTION_DIMENSION_OUT_OF_SCOPE",
        "RESEARCH_AGENT_DRILLDOWN_HIERARCHY_NOT_FOUND",
        "RESEARCH_AGENT_DRILLDOWN_NOT_ADJACENT",
        "RESEARCH_AGENT_DRILLDOWN_DIMENSION_NOT_FOUND",
        "RESEARCH_AGENT_DRILLDOWN_NEXT_DIMENSION_REQUIRED",
        "RESEARCH_AGENT_ORDER_REF_NOT_IN_QUERY",
        "RESEARCH_AGENT_EVIDENCE_VERSION_MISMATCH",
        "RESEARCH_AGENT_EVIDENCE_METRIC_OUT_OF_SCOPE",
        "RESEARCH_AGENT_EVIDENCE_DIMENSION_OUT_OF_SCOPE",
        "RESEARCH_AGENT_EVIDENCE_FILTER_OUT_OF_SCOPE",
        "RESEARCH_AGENT_EVIDENCE_DEPENDENCY_ITERATION_MISMATCH",
    }
)
_SEMANTIC_CAPABILITY_CODES = frozenset(
    {
        "SEMANTIC_QUERY_DIMENSION_CAPABILITY_NOT_FOUND",
        "SEMANTIC_QUERY_DIMENSION_PHYSICAL_BINDING_MISSING",
        "SEMANTIC_QUERY_FILTER_PHYSICAL_BINDING_MISSING",
        "SEMANTIC_QUERY_LOGICAL_DIMENSION_NOT_FOUND",
        "SEMANTIC_QUERY_PLAN_NOT_PROVEN",
        "SEMANTIC_SQL_TIME_BUCKET_UNSUPPORTED",
        "SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED",
        "SEMANTIC_SQL_TIME_RANGE_UNSUPPORTED",
        "SEMANTIC_SQL_RELATION_CONTRACT_REQUIRED",
        "SEMANTIC_SQL_RELATION_CONTRACT_NOT_READY",
        "SEMANTIC_SQL_MANY_TO_MANY_JOIN_FORBIDDEN",
    }
)


def _runtime_failure(
    code: str,
    *,
    stage: ToolFailureStage,
    retryable: bool = False,
    parameter_retryable: bool = False,
    same_parameter_retryable: bool = False,
    capability_gap: bool = False,
    sql_escalation_allowed: bool = False,
) -> SemanticQueryRuntimeError:
    """把内部预期错误冻结为 Runtime 的结构化业务错误。"""

    error_code = _BOUNDARY_ERROR_CODES.get(code)
    if error_code is ToolErrorCode.UNSUPPORTED_CAPABILITY:
        capability_gap = True
    if retryable:
        parameter_retryable = True
    return SemanticQueryRuntimeError(
        error_code.value if error_code is not None else code,
        failure_stage=stage.value,
        retryable=retryable or parameter_retryable or same_parameter_retryable,
        parameter_retryable=parameter_retryable,
        same_parameter_retryable=same_parameter_retryable,
        capability_gap=capability_gap,
        sql_escalation_allowed=(
            sql_escalation_allowed
            and error_code is ToolErrorCode.UNSUPPORTED_CAPABILITY
        ),
        details=(
            {"internal_code": code}
            if error_code is not None and error_code.value != code
            else None
        ),
    )


class SemanticQueryRuntime:
    """校验冻结运行边界后调用共享分析执行服务。"""

    def __init__(
        self,
        execution_service: AnalysisExecutionService,
        query_builder: SemanticQueryBuilder | None = None,
        *,
        execution_state_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._execution_service = execution_service
        self._query_builder = query_builder
        # 同一请求作用域的 AnalysisExecutionService 绑定同一个 EventPublisher
        # 和会话；Research 批次虽然可以并发准备动作，但底层执行必须串行，
        # 防止共享会话同时分配相同事件序号。
        self._execution_lock = RLock()
        # 阶段 7：宿主可注入状态投影（如 shadow 的 ResearchExecutionState），
        # 把工具上下文适配成执行服务要求的 run 状态表面；默认保持原样。
        self._execution_state_factory = execution_state_factory

    def fork_with_session(self, session: Any) -> SemanticQueryRuntime:
        """复制运行时，并把执行服务绑定到独立 Session。"""

        return SemanticQueryRuntime(
            self._execution_service.fork_with_session(session),
            self._query_builder,
            execution_state_factory=self._execution_state_factory,
        )

    def execute(
        self,
        context: Any,
        query: ResearchSemanticQuery,
        requirement: ResearchAgentRequirement | None = None,
        evidence: Collection[ResearchEvidence] = (),
        *,
        plan_id: str | None = None,
        iteration: int = 0,
    ) -> ResearchSemanticQueryOutcome:
        """执行一次 Semantic Query；该入口不自动修改参数或重试。"""

        resolved_plan_id = plan_id or self._plan_id(query)
        # 失败观察的内部定位码：error_code 被映射成粗粒度值（如
        # PERMISSION_DENIED）时，观察侧靠它区分是哪道门在拦（run 1306）。
        internal_code: str | None = None
        try:
            semantic_scope = self._semantic_scope(context)
            resolved_requirement = requirement or self._requirement(context)
            try:
                self._validate_runtime_boundary(
                    context,
                    query,
                    resolved_requirement,
                    semantic_scope,
                    evidence,
                )
            except SemanticForbiddenError:
                raise
            except SemanticQueryBoundaryError as exc:
                raise _runtime_failure(
                    exc.code,
                    stage=ToolFailureStage.VALIDATION,
                ) from exc
            except ValueError as exc:
                code = str(exc)
                if code not in _CONTRACT_VALIDATION_CODES:
                    raise
                raise _runtime_failure(
                    code,
                    stage=ToolFailureStage.VALIDATION,
                ) from exc
            builder = self._query_builder or SemanticQueryBuilder(
                semantic_scope=semantic_scope,
                schema_snapshot=(
                    semantic_scope.schema_snapshot
                    if semantic_scope is not None
                    else None
                ),
                evidence_value_resolver=(
                    lambda value_ref, evidence_item: self._resolve_evidence_value(
                        context,
                        value_ref,
                        evidence_item,
                    )
                ),
            )
            try:
                spec = builder.build(
                    query,
                    requirement=resolved_requirement,
                    evidence=evidence,
                    semantic_scope=semantic_scope,
                    evidence_value_resolver=(
                        lambda value_ref, evidence_item: self._resolve_evidence_value(
                            context,
                            value_ref,
                            evidence_item,
                        )
                    ),
                )
            except SemanticQueryRuntimeError:
                raise
            except SemanticQueryBuildError as exc:
                code = exc.code
                if code == "UNSUPPORTED_CAPABILITY":
                    raise _runtime_failure(
                        code,
                        stage=ToolFailureStage.PLANNING,
                        retryable=True,
                        capability_gap=True,
                    ) from exc
                raise _runtime_failure(
                    code,
                    stage=ToolFailureStage.PLANNING,
                ) from exc
            raw_outcome = self._execute_spec(
                context,
                spec,
                resolved_plan_id,
            )
            try:
                return self._project_success(
                    query,
                    resolved_plan_id,
                    spec,
                    raw_outcome,
                    iteration=iteration,
                )
            except PlanPipelineError as exc:
                raise self._execution_failure(exc) from exc
            except SemanticQueryProjectionError as exc:
                raise _runtime_failure(
                    exc.code,
                    stage=ToolFailureStage.PROJECTION,
                ) from exc
        except SemanticForbiddenError as exc:
            code = ToolErrorCode.PERMISSION_DENIED
            stage = ToolFailureStage.PERMISSION
            retryable = False
            parameter_retryable = False
            same_parameter_retryable = False
            capability_gap = False
            message = str(exc) or exc.__class__.__name__
            internal_code = message
        except SemanticQueryRuntimeError as exc:
            (
                code,
                stage,
                retryable,
                parameter_retryable,
                same_parameter_retryable,
                capability_gap,
                _sql_escalation_allowed,
            ) = self._runtime_error_metadata(exc)
            message = str(exc) or exc.__class__.__name__
            details = getattr(exc, "details", None)
            if isinstance(details, dict):
                internal_code = details.get("internal_code")
        except PlanPipelineError as exc:
            (
                code,
                stage,
                retryable,
                parameter_retryable,
                same_parameter_retryable,
                capability_gap,
                _sql_escalation_allowed,
            ) = self._execution_failure_metadata(exc)
            message = str(exc) or exc.__class__.__name__
        except (ResultArtifactReadError, ResultArtifactWriteError) as exc:
            code = ToolErrorCode.RESULT_STORE_FAILED
            stage = ToolFailureStage.PERSISTENCE
            retryable = True
            parameter_retryable = False
            same_parameter_retryable = True
            capability_gap = False
            message = str(exc) or exc.__class__.__name__
        except SemanticValidationError as exc:
            semantic_code = exc.detail
            internal_code = semantic_code
            if semantic_code in _SEMANTIC_CAPABILITY_CODES:
                code = ToolErrorCode.UNSUPPORTED_CAPABILITY
                stage = ToolFailureStage.PLANNING
                retryable = True
                parameter_retryable = True
                same_parameter_retryable = False
                capability_gap = True
            elif semantic_code == "SEMANTIC_PLAN_PERMISSION_DENIED":
                code = ToolErrorCode.PERMISSION_DENIED
                stage = ToolFailureStage.PERMISSION
                retryable = False
                parameter_retryable = False
                same_parameter_retryable = False
                capability_gap = False
            else:
                # 未知但稳定的语义校验码必须原样返回，模型和日志才能定位真实约束。
                code = semantic_code or ToolErrorCode.INVALID_REQUEST.value
                stage = ToolFailureStage.VALIDATION
                retryable = False
                parameter_retryable = False
                same_parameter_retryable = False
                capability_gap = False
            message = semantic_code or exc.__class__.__name__
        return ResearchSemanticQueryOutcome(
            run_id=query.run_id,
            status="cancelled" if code is ToolErrorCode.CANCELLED else "failed",
            plan_id=resolved_plan_id,
            error_code=code,
            failure_stage=stage,
            retryable=retryable,
            parameter_retryable=parameter_retryable,
            same_parameter_retryable=same_parameter_retryable,
            capability_gap=capability_gap,
            # 阶段 2不开放裸 SQL，即使语义能力不足也不能在本入口升级。
            sql_escalation_allowed=False,
            message=message,
            internal_code=internal_code,
        )

    def _execute_spec(self, context: Any, spec: Any, plan_id: str) -> Any:
        """兼容同步返回值和带事件生成器的执行服务。"""

        execution_state = (
            self._execution_state_factory(context)
            if self._execution_state_factory is not None
            else context
        )
        # ResearchExecutionState 是外层 Plan 节点的内部执行投影。AnalysisExecutionService
        # 会生成确定性的查询执行计划，但该计划不拥有 Run 的根计划状态；执行期间临时
        # 移开根计划键，结束后无论成功失败都原样恢复，避免内部计划覆盖外层 Planner DAG。
        projected = execution_state is not context
        state = getattr(getattr(execution_state, "context", None), "state", None)
        missing = object()
        preserved: dict[str, Any] = {}
        if projected and isinstance(state, dict):
            for key in ("analysis_plan", PLAN_EXECUTION_STATE_KEY):
                preserved[key] = state.pop(key, missing)
        try:
            with self._execution_lock:
                result = self._execution_service.execute(
                    execution_state,
                    spec,
                    plan_id=plan_id,
                )
                if not isinstance(result, Generator):
                    return result
                while True:
                    try:
                        next(result)
                    except StopIteration as completed:
                        return completed.value
        finally:
            if projected and isinstance(state, dict):
                for key, value in preserved.items():
                    if value is missing:
                        state.pop(key, None)
                    else:
                        state[key] = value

    def _validate_runtime_boundary(
        self,
        context: Any,
        query: ResearchSemanticQuery,
        requirement: ResearchAgentRequirement | None,
        semantic_scope: Any,
        evidence: Collection[ResearchEvidence],
    ) -> None:
        if requirement is None:
            raise SemanticQueryBoundaryError("RESEARCH_AGENT_REQUIREMENT_REQUIRED")
        requirement.validate_query(query, evidence)
        for evidence_item in evidence:
            evidence_item.validate_against(requirement, evidence)
        inner = getattr(context, "context", context)
        if semantic_scope is None:
            raise SemanticQueryBoundaryError("SEMANTIC_SCOPE_REQUIRED")
        state = getattr(inner, "state", {})
        if not isinstance(state, dict):
            raise SemanticQueryBoundaryError("RESEARCH_AGENT_CONTEXT_RUN_REQUIRED")
        context_run_id = state.get("research_run_id")
        if not isinstance(context_run_id, str) or not context_run_id:
            raise SemanticQueryBoundaryError("RESEARCH_AGENT_CONTEXT_RUN_REQUIRED")
        if context_run_id != requirement.run_id:
            raise SemanticForbiddenError("RESEARCH_AGENT_CONTEXT_RUN_MISMATCH")
        if int(getattr(inner, "oid", 0) or 0) != semantic_scope.workspace_id:
            raise SemanticForbiddenError("TENANT_SCOPE_MISMATCH")
        if int(getattr(inner, "user_id", 0) or 0) != semantic_scope.user_id:
            raise SemanticForbiddenError("USER_SCOPE_MISMATCH")
        if int(getattr(inner, "datasource_id", 0) or 0) != semantic_scope.datasource_id:
            raise SemanticForbiddenError("DATASOURCE_SCOPE_MISMATCH")
        dataset_id = getattr(inner, "dataset_id", None)
        if dataset_id != semantic_scope.dataset_id:
            raise SemanticQueryBoundaryError("DATASET_SCOPE_MISMATCH")
        if query.version_snapshot.schema_version != self._schema_version(
            semantic_scope
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_SCHEMA_VERSION_CHANGED")
        if query.version_snapshot.contract_version != self._contract_version(
            semantic_scope
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_CONTRACT_VERSION_CHANGED")
        schema_snapshot = getattr(semantic_scope, "schema_snapshot", None)
        schema_dataset_id = getattr(
            getattr(schema_snapshot, "data_set", None), "id", None
        )
        if (
            isinstance(schema_dataset_id, int)
            and schema_dataset_id != semantic_scope.dataset_id
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_SCHEMA_DATASET_MISMATCH")
        schema_fingerprint = getattr(schema_snapshot, "schema_fingerprint", None)
        if not isinstance(schema_fingerprint, str) or not schema_fingerprint:
            raise SemanticQueryBoundaryError("SEMANTIC_SCHEMA_SNAPSHOT_REQUIRED")
        if query.version_snapshot.schema_fingerprint != schema_fingerprint:
            raise SemanticQueryBoundaryError("SEMANTIC_SCHEMA_FINGERPRINT_CHANGED")
        dataset_ref = self._dataset_ref_id(requirement.scope.dataset_ref)
        if dataset_ref != semantic_scope.dataset_id:
            raise SemanticQueryBoundaryError("DATASET_SCOPE_MISMATCH")
        scope_fingerprint = getattr(semantic_scope, "scope_fingerprint", None)
        if not isinstance(scope_fingerprint, str) or not scope_fingerprint:
            raise SemanticQueryBoundaryError("SEMANTIC_SCOPE_FINGERPRINT_REQUIRED")
        if scope_fingerprint != requirement.scope.scope_fingerprint:
            raise SemanticQueryBoundaryError("SEMANTIC_PERMISSION_FINGERPRINT_CHANGED")
        scope_permission_fingerprint = getattr(
            semantic_scope, "permission_fingerprint", None
        )
        if (
            not isinstance(scope_permission_fingerprint, str)
            or not scope_permission_fingerprint
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_PERMISSION_FINGERPRINT_REQUIRED")
        if (
            scope_permission_fingerprint
            != requirement.version_snapshot.permission_fingerprint
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_PERMISSION_FINGERPRINT_CHANGED")
        context_permission_version = getattr(inner, "permission_version", None)
        scope_permission_version = getattr(semantic_scope, "permission_version", None)
        # permission_version 是检索层资产级授权（P2-4）的版本钩子：当前索引投影
        # 与路由期检索请求都尚未填充，端到端为 None，legacy 路径也从不校验。
        # 门禁因此只在平台真正提供版本时强制一致——任一侧有值而另一侧缺失、
        # 或两侧值不同，都是边界破坏；两侧同时缺失是当前栈的合法形态，
        # 权限仍由 permission_fingerprint 门与 authorized_tables 完整约束。
        if (context_permission_version or scope_permission_version) and (
            context_permission_version != scope_permission_version
        ):
            raise SemanticQueryBoundaryError("SEMANTIC_PERMISSION_VERSION_CHANGED")

    @staticmethod
    def _semantic_scope(context: Any) -> Any:
        inner = getattr(context, "context", context)
        scope = getattr(inner, "semantic_asset_scope", None)
        if scope is not None:
            return scope
        state = getattr(inner, "state", {})
        value = state.get("semantic_scope") if isinstance(state, dict) else None
        if value is None:
            return None
        return SemanticAssetScope.model_validate(value)

    @staticmethod
    def _requirement(context: Any) -> ResearchAgentRequirement | None:
        inner = getattr(context, "context", context)
        state = getattr(inner, "state", {})
        value = (
            state.get("research_agent_requirement") if isinstance(state, dict) else None
        )
        if value is None:
            return None
        if isinstance(value, ResearchAgentRequirement):
            return value
        return ResearchAgentRequirement.model_validate(value)

    def _project_success(
        self,
        query: ResearchSemanticQuery,
        plan_id: str,
        spec: Any,
        raw_outcome: Any,
        *,
        iteration: int,
    ) -> ResearchSemanticQueryOutcome:
        if raw_outcome is None:
            raise PlanPipelineError("PLAN_EXECUTION_CANCELLED")
        records = getattr(raw_outcome, "execution_records", None)
        if not isinstance(records, dict):
            raise SemanticQueryProjectionError(
                "RESEARCH_AGENT_EXECUTION_OUTCOME_INVALID"
            )
        result_refs = tuple(
            ResearchResultRef(
                run_id=query.run_id, result_id=str(result.get("result_set_id"))
            )
            for result in records.values()
            if isinstance(result, dict) and result.get("result_set_id")
        )
        primary = getattr(raw_outcome, "primary_execution", None)
        if not isinstance(primary, dict):
            raise SemanticQueryProjectionError("RESEARCH_AGENT_PRIMARY_RESULT_MISSING")
        primary_id = str(primary.get("result_set_id") or "")
        if not primary_id:
            raise SemanticQueryProjectionError("RESEARCH_AGENT_PRIMARY_RESULT_MISSING")
        rows = getattr(raw_outcome, "primary_rows", None)
        normalized_rows = tuple(
            dict(item) for item in rows or () if isinstance(item, dict)
        )
        if not normalized_rows:
            return ResearchSemanticQueryOutcome(
                run_id=query.run_id,
                status="failed",
                plan_id=plan_id,
                result_refs=result_refs,
                primary_result_id=primary_id,
                error_code=ToolErrorCode.EMPTY_RESULT,
                failure_stage=ToolFailureStage.PROJECTION,
                retryable=False,
                message="查询结果为空",
            )
        evidence = self._build_evidence(
            query,
            plan_id,
            primary,
            normalized_rows,
            logical_result_columns=spec.runtime.get("logical_result_columns"),
            iteration=iteration,
        )
        return ResearchSemanticQueryOutcome(
            run_id=query.run_id,
            status="succeeded",
            plan_id=plan_id,
            result_refs=result_refs,
            evidence=(evidence,),
            primary_result_id=primary_id,
        )

    @staticmethod
    def _build_evidence(
        query: ResearchSemanticQuery,
        plan_id: str,
        primary: dict[str, Any],
        rows: tuple[dict[str, Any], ...],
        *,
        logical_result_columns: Any,
        iteration: int,
    ) -> ResearchEvidence:
        evidence_id = f"evidence:{plan_id}"
        if not isinstance(logical_result_columns, list) or not logical_result_columns:
            raise SemanticQueryProjectionError(
                "RESEARCH_AGENT_RESULT_COLUMN_MAPPING_REQUIRED"
            )
        try:
            logical_columns = tuple(
                ResearchLogicalColumn.model_validate(item)
                for item in logical_result_columns
            )
        except (TypeError, ValueError) as exc:
            raise SemanticQueryProjectionError(
                "RESEARCH_AGENT_RESULT_COLUMN_MAPPING_INVALID"
            ) from exc
        available_fields = set(primary.get("fields") or rows[0])
        missing_fields = {
            item.result_field
            for item in logical_columns
            if item.result_field not in available_fields
        }
        if missing_fields:
            raise SemanticQueryProjectionError(
                "RESEARCH_AGENT_RESULT_COLUMN_MAPPING_INVALID"
            )
        return ResearchEvidence(
            run_id=query.run_id,
            evidence_id=evidence_id,
            source_tool_call=ResearchToolCallRef(
                run_id=query.run_id,
                tool_call_id=f"semantic-query:{plan_id}",
            ),
            result_ref=ResearchResultRef(
                run_id=query.run_id,
                result_id=str(primary["result_set_id"]),
            ),
            iteration=iteration,
            purpose=query.purpose,
            metric_refs=query.metrics,
            dimension_refs=query.dimensions,
            time_grain=query.time_grain,
            time_ranges=query.time_ranges,
            operations=_query_operations(query),
            filters=query.filters,
            logical_columns=logical_columns,
            statistics=ResearchEvidenceStatistics(row_count=len(rows)),
            sample_rows=rows[:10],
            hypothesis_ids=query.hypothesis_ids,
            version_snapshot=query.version_snapshot,
        )
    @staticmethod
    def _schema_version(scope: Any) -> int:
        snapshot = getattr(scope, "schema_snapshot", None)
        value = getattr(snapshot, "schema_version", None)
        if not isinstance(value, int):
            raise SemanticQueryBoundaryError("SEMANTIC_SCHEMA_SNAPSHOT_REQUIRED")
        return value

    @staticmethod
    def _contract_version(scope: Any) -> int:
        snapshot = getattr(scope, "schema_snapshot", None)
        value = getattr(snapshot, "contract_version", None)
        if not isinstance(value, int):
            raise SemanticQueryBoundaryError("SEMANTIC_CONTRACT_SNAPSHOT_REQUIRED")
        return value

    @staticmethod
    def _plan_id(query: ResearchSemanticQuery) -> str:
        payload = json.dumps(
            query.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"research-query-{hashlib.sha256(payload).hexdigest()[:16]}"

    @staticmethod
    def _dataset_ref_id(dataset_ref: str) -> int:
        """严格解析 Dataset 引用，避免 24 错配到 243。"""

        parts = dataset_ref.split(":")
        if len(parts) != 3 or parts[0] != "ASSET" or parts[1] != "dataset":
            raise SemanticQueryBoundaryError("DATASET_SCOPE_MISMATCH")
        if not parts[2].isdigit() or int(parts[2]) <= 0:
            raise SemanticQueryBoundaryError("DATASET_SCOPE_MISMATCH")
        return int(parts[2])

    @staticmethod
    def _execution_failure(exc: PlanPipelineError) -> SemanticQueryRuntimeError:
        (
            code,
            stage,
            retryable,
            parameter_retryable,
            same_parameter_retryable,
            capability_gap,
            sql_escalation_allowed,
        ) = SemanticQueryRuntime._execution_failure_metadata(exc)
        return SemanticQueryRuntimeError(
            code.value,
            failure_stage=stage.value,
            retryable=retryable,
            parameter_retryable=parameter_retryable,
            same_parameter_retryable=same_parameter_retryable,
            capability_gap=capability_gap,
            sql_escalation_allowed=sql_escalation_allowed,
        )

    @staticmethod
    def _execution_failure_metadata(
        exc: PlanPipelineError,
    ) -> tuple[
        ToolErrorCode,
        ToolFailureStage,
        bool,
        bool,
        bool,
        bool,
        bool,
    ]:
        """按稳定的 Plan 错误码映射 Runtime 元数据，不依赖自然语言文本。"""

        code = exc.code
        if code in {
            "budget_exhausted",
            "PLAN_BUDGET_EXHAUSTED",
            "PLAN_CLARIFICATION_BUDGET_EXHAUSTED",
        }:
            return (
                ToolErrorCode.BUDGET_EXHAUSTED,
                ToolFailureStage.BUDGET,
                False,
                False,
                False,
                False,
                False,
            )
        if code in {
            "PLAN_EXECUTION_CANCELLED",
            "PLAN_QUERY_CANCELLED",
        }:
            return (
                ToolErrorCode.CANCELLED,
                ToolFailureStage.EXECUTION,
                False,
                False,
                False,
                False,
                False,
            )
        if code in {
            "PLAN_QUERY_RESULT_STORE_REQUIRED",
            "PLAN_RESULT_STORE_REQUIRED",
            "PLAN_INPUT_RESULT_SETS_MISSING",
            "PLAN_INPUT_RESULT_SET_MISSING",
        }:
            return (
                ToolErrorCode.RESULT_STORE_FAILED,
                ToolFailureStage.PERSISTENCE,
                True,
                False,
                True,
                False,
                False,
            )
        if code in {
            "PLAN_COMPILE_RESULT_MISSING",
            "PLAN_QUERY_TASK_NOT_PROVEN",
            "semantic_query_plan_required",
            "semantic_query_plan_fingerprint_mismatch",
            "semantic_query_plan_not_proven",
            "semantic_query_plan_incomplete",
            "semantic_compile_failed",
        }:
            return (
                ToolErrorCode.SQL_COMPILE_FAILED,
                ToolFailureStage.COMPILATION,
                True,
                True,
                False,
                False,
                False,
            )
        if code in {
            "sql_validation_failed",
            "invalid_sql",
            "unsafe_sql",
            "sql_not_read_only",
            "multiple_statements_not_allowed",
            "PLAN_VALIDATE_SQL_FAILED",
        }:
            return (
                ToolErrorCode.SQL_VALIDATION_FAILED,
                ToolFailureStage.COMPILATION,
                True,
                True,
                False,
                False,
                False,
            )
        if code in {
            "PLAN_STRICT_QUERY_PLAN_MISSING",
            "PLAN_STRICT_QUERY_PLAN_NOT_PROVEN",
            "PLAN_PROOF_FAILED",
            "PLAN_VALIDATION_FAILED",
        }:
            return (
                ToolErrorCode.SEMANTIC_PLAN_REJECTED,
                ToolFailureStage.PROOF,
                True,
                True,
                False,
                False,
                False,
            )
        if code in {
            "COMPUTE_CONTRIBUTION_RECONCILIATION_FAILED",
            "PLAN_RECONCILIATION_FAILED",
        }:
            return (
                ToolErrorCode.RECONCILIATION_FAILED,
                ToolFailureStage.PROJECTION,
                False,
                False,
                False,
                False,
                False,
            )
        if code in {
            "FANOUT_DETECTED",
            "PLAN_FANOUT_DETECTED",
            "COMPUTE_FANOUT_DETECTED",
        }:
            return (
                ToolErrorCode.FANOUT_DETECTED,
                ToolFailureStage.PROJECTION,
                False,
                False,
                False,
                False,
                False,
            )
        if code in {
            "RESULT_CONTRACT_FAILED",
            "PLAN_RESULT_CONTRACT_FAILED",
            "PLAN_PRIMARY_RESULT_MISSING",
            "PLAN_PRIMARY_RESULT_FAILED",
            "PLAN_QUERY_RESULT_MISSING",
        }:
            return (
                ToolErrorCode.RESULT_CONTRACT_FAILED,
                ToolFailureStage.PROJECTION,
                False,
                False,
                False,
                False,
                False,
            )
        if code in {
            "PLAN_QUERY_TIMEOUT",
            "QUERY_TIMEOUT",
        }:
            return (
                ToolErrorCode.EXECUTION_TIMEOUT,
                ToolFailureStage.EXECUTION,
                True,
                False,
                True,
                False,
                False,
            )
        return (
            ToolErrorCode.EXECUTION_FAILED,
            ToolFailureStage.EXECUTION,
            True,
            False,
            False,
            False,
            False,
        )

    @staticmethod
    def _runtime_error_metadata(
        exc: SemanticQueryRuntimeError,
    ) -> tuple[
        ToolErrorCode,
        ToolFailureStage,
        bool,
        bool,
        bool,
        bool,
        bool,
    ]:
        try:
            code = ToolErrorCode(exc.code)
        except ValueError:
            code = ToolErrorCode.INVALID_REQUEST
        try:
            stage = ToolFailureStage(exc.failure_stage)
        except ValueError:
            stage = ToolFailureStage.EXECUTION
        return (
            code,
            stage,
            exc.retryable,
            exc.parameter_retryable,
            exc.same_parameter_retryable,
            exc.capability_gap,
            exc.sql_escalation_allowed,
        )

    def _resolve_evidence_value(
        self,
        context: Any,
        value_ref: Any,
        evidence: ResearchEvidence,
    ) -> Any:
        """从当前 Run 的完整 ResultStore 结果读取筛选值。"""

        value_ref.validate_against(evidence)
        inner = getattr(context, "context", context)
        result_store = getattr(inner, "result_store", None)
        state = getattr(inner, "state", {})
        result_sets = state.get("result_sets") if isinstance(state, dict) else None
        if result_store is None or not isinstance(result_sets, dict):
            raise _runtime_failure(
                "RESULT_STORE_FAILED",
                stage=ToolFailureStage.PERSISTENCE,
                retryable=True,
            )
        payload = result_sets.get(evidence.result_ref.result_id)
        if payload is None:
            for candidate in result_sets.values():
                if (
                    isinstance(candidate, dict)
                    and candidate.get("result_set_id") == evidence.result_ref.result_id
                ):
                    payload = candidate
                    break
        if payload is None:
            raise _runtime_failure(
                "RESULT_STORE_FAILED",
                stage=ToolFailureStage.PERSISTENCE,
                retryable=True,
            )
        try:
            result_ref = ResultSetRef.model_validate(payload)
            if result_ref.result_set_id != evidence.result_ref.result_id:
                raise ValueError("RESULT_STORE_RESULT_ID_MISMATCH")
            execution_id = getattr(inner, "execution_id", None)
            chat_id = getattr(inner, "chat_id", None)
            record_id = getattr(inner, "record_id", None)
            if (
                not isinstance(execution_id, str)
                or not execution_id
                or not isinstance(chat_id, int)
                or not isinstance(record_id, int)
            ):
                raise ValueError("RESULT_STORE_OWNERSHIP_REQUIRED")
            snapshot = result_store.read(
                result_ref,
                execution_id=execution_id,
                execution_type=ChatRecordExecutionType.AGENT,
                chat_id=chat_id,
                record_id=record_id,
            )
        except (ResultArtifactReadError, ValueError) as exc:
            raise _runtime_failure(
                "RESULT_STORE_FAILED",
                stage=ToolFailureStage.PERSISTENCE,
                retryable=True,
            ) from exc
        field = self._result_field(value_ref.column_ref, evidence, result_ref.fields)
        rows = list(snapshot.rows)
        if value_ref.row_selector.order_by is not None:
            order_field = self._result_field(
                value_ref.row_selector.order_by,
                evidence,
                result_ref.fields,
            )
            reverse = value_ref.row_selector.direction.value == "desc"
            try:
                rows.sort(
                    key=lambda row: (
                        row.get(order_field) is None,
                        row.get(order_field),
                    ),
                    reverse=reverse,
                )
            except TypeError as exc:
                raise _runtime_failure(
                    "EVIDENCE_REFERENCE_INVALID",
                    stage=ToolFailureStage.VALIDATION,
                ) from exc
        index = value_ref.row_selector.rank - 1
        if index < 0 or index >= len(rows) or field not in rows[index]:
            raise _runtime_failure(
                "EVIDENCE_REFERENCE_INVALID",
                stage=ToolFailureStage.PROJECTION,
            )
        return rows[index][field]

    @staticmethod
    def _result_field(
        column_ref: str,
        evidence: ResearchEvidence,
        fields: tuple[str, ...],
    ) -> str:
        """按 Evidence 冻结映射选择结果列，禁止依赖字段位置。"""

        matches = [
            item for item in evidence.logical_columns if item.asset_ref == column_ref
        ]
        if len(matches) != 1:
            raise _runtime_failure(
                "EVIDENCE_REFERENCE_INVALID",
                stage=ToolFailureStage.VALIDATION,
            )
        result_field = matches[0].result_field
        if isinstance(result_field, str) and result_field in fields:
            return result_field
        raise _runtime_failure(
            "EVIDENCE_REFERENCE_INVALID",
            stage=ToolFailureStage.PROJECTION,
        )


def _query_operations(query: ResearchSemanticQuery) -> tuple[SemanticOperation, ...]:
    """从实际执行参数推导已完成操作，Evidence 不接受模型自行声明。"""

    operations: list[SemanticOperation] = [
        SemanticOperation(type="group", target_ref=ref) for ref in query.dimensions
    ]
    if query.time_grain is not None:
        operations.append(SemanticOperation(type="group", time_grain=query.time_grain))
    operations.extend(
        SemanticOperation(
            type="sort",
            target_ref=item.ref,
            direction=item.direction.value,
        )
        for item in query.order
    )
    operations.append(SemanticOperation(type="limit", value=query.limit))
    if query.comparison is not ResearchQueryComparison.NONE:
        operations.append(
            SemanticOperation(
                type="calculate",
                calculation=CalculationOperation(query.comparison.value),
            )
        )
    return tuple(operations)


def semantic_query_plan_id(query: ResearchSemanticQuery) -> str:
    """与 Runtime 默认计划 ID 一致的确定性指纹，供工具层做去重判断。"""

    return SemanticQueryRuntime._plan_id(query)


_STATE_EXCLUDED_KEYS = frozenset(
    {
        "analysis_plan",
        PLAN_EXECUTION_STATE_KEY,
        "full_data",
        "tool_offloads",
        "semantic_schema",
    }
)


class _ExecutionDeadlineBudget:
    """把新契约 ``ResearchBudget`` 适配成执行服务的墙钟预算表面。

    ``AnalysisExecutionService`` 读取 ``state.budget.remaining_seconds()`` 计算
    查询任务截止（run 1278 冒烟回归：投影态缺 budget 直接 AttributeError）。
    研究循环的整跑时长由 Harness 按 ``max_duration_seconds`` 单独约束，这里
    以每次执行构造时刻起算，只保证单次查询的截止不超过剩余时长上限。
    """

    def __init__(self, budget: Any) -> None:
        self._budget = budget
        self._started_at = time.monotonic()

    def remaining_seconds(self) -> float:
        limit = float(getattr(self._budget, "max_duration_seconds", 0) or 0)
        if limit <= 0:
            return float("inf")
        return max(limit - (time.monotonic() - self._started_at), 0.0)


@dataclass
class ResearchExecutionState:
    """把 ResearchToolContext 投影成 AnalysisExecutionService 的状态表面。

    字段口径与 ``AgentRuntimeState`` 一致：working state 与会话来自底层
    AgentToolContext，事件、Trace、计划快照和结果工件因此全部落在当前
    Run 身份下。（原属 shadow 栈；阶段 8 shadow 删除后由主路径唯一使用，
    随迁入本模块。）
    """

    ctx: Any
    run: ChatbiAgentRun
    record: Any

    def require_run_id(self) -> int:
        if self.run.id is None:
            raise RuntimeError("AGENT_RUN_ID_MISSING")
        return self.run.id

    @property
    def budget(self) -> Any:
        """执行服务读取的墙钟预算表面；见 :class:`_ExecutionDeadlineBudget`。"""

        return _ExecutionDeadlineBudget(getattr(self.ctx, "budget", None))

    @property
    def context(self) -> Any:
        return self.ctx.context

    @property
    def cancellation(self) -> Any:
        return self.ctx.cancellation or NeverCancelled()

    def persistable_context(self) -> dict[str, Any]:
        """与 AgentRuntimeState.persistable_context 同口径的持久化快照。"""

        state = self.ctx.context.state
        if not isinstance(state, dict):
            return {}
        return {
            key: value
            for key, value in state.items()
            if key not in _STATE_EXCLUDED_KEYS
        }


__all__ = [
    "ResearchExecutionState",
    "SemanticQueryRuntime",
    "semantic_query_plan_id",
]
