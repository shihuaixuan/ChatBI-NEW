"""阶段 2 Semantic Query Runtime 的 dataset 243 真实执行验收。

该脚本不调用 LLM，也不构造 ResearchAction。它直接使用冻结的 Research Agent
Requirement、Semantic Query Runtime、严格语义规划、SQL 编译、DAG 执行、ComputeEngine
和 ResultStore，覆盖比较、层级下钻、贡献度和驱动验证。

用法：
    python scripts/run_semantic_runtime_dataset243.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Literal

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session  # noqa: E402

from apps.chatbi.composition import (  # noqa: E402
    build_agent_event_publisher,
    build_chat_application_service,
    build_chat_record_service,
    build_query_service,
    build_result_artifact_service,
    configure_agent_cleanup,
)
from apps.chatbi.models import AgentConfig, AgentRunStatus  # noqa: E402
from apps.chatbi.models.dto.research_agent import (  # noqa: E402
    ResearchAgentRequirement,
    ResearchDrilldownSpec,
    ResearchDriverRelationship,
    ResearchEvidence,
    ResearchEvidenceRequirement,
    ResearchEvidenceValueRef,
    ResearchHierarchy,
    ResearchOrderDirection,
    ResearchQueryComparison,
    ResearchReason,
    ResearchRowSelector,
    ResearchScope,
    ResearchSemanticQuery,
    ResearchTimeBinding,
    ResearchTimeRole,
    ResearchVersionSnapshot,
)
from apps.chatbi.orchestration.agent.state import (  # noqa: E402
    AgentRuntimeStateFactory,
)
from apps.chatbi.orchestration.agent.tool_results import (  # noqa: E402
    ChatBIToolResultProcessor,
)
from apps.chatbi.orchestration.agent.tools.base import (  # noqa: E402
    AgentToolContextServices,
)
from apps.chatbi.repository.sqlmodel import agent_run_repository  # noqa: E402
from apps.chatbi.repository.sqlmodel.agent_run_repository import (  # noqa: E402
    AgentExecutionDeletionService,
)
from apps.chatbi.services.computation import ComputeEngine  # noqa: E402
from apps.chatbi.services.execution import (  # noqa: E402
    AnalysisExecutionDependencies,
    AnalysisExecutionService,
    QueryTaskExecutor,
    ResultStore,
)
from apps.chatbi.services.research.semantic_runtime import (  # noqa: E402
    SemanticQueryRuntime,
)
from apps.conversation import (  # noqa: E402
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordStatus,
    CreateChat,
)
from apps.retrieval import (  # noqa: E402
    ExecutableAssetReference,
    RetrievalResourceType,
)
from apps.semantic.composition import (  # noqa: E402
    build_semantic_dataset_catalog_service,
    build_semantic_schema_service,
    build_semantic_sql_compilation_service,
)
from apps.temporal import build_dataset_temporal_context  # noqa: E402
from apps.tool import ToolRegistry, default_middlewares  # noqa: E402
from apps.tool.tools.datasource import ValidateSqlTool  # noqa: E402
from apps.tool.tools.semantic import CompileSemanticSqlTool  # noqa: E402
from apps.tool.tools.semantic_contracts import SemanticAssetScope  # noqa: E402
from common.core.db import engine  # noqa: E402

OID = 1
USER_ID = 1
DATASET_ID = 243
DATASOURCE_ID = 13
MODEL_ID = 246

GMV_REF = "METRIC:271:246"
ORDER_COUNT_REF = "METRIC:265:246"
AOV_REF = "METRIC:303:246"
TIME_REF = "DIMENSION:276:246"
SELLER_REF = "DIMENSION:277:246"
STALL_REF = "DIMENSION:278:246"

PERMISSION_VERSION = "dataset243-stage2-v1"
PERMISSION_FINGERPRINT = "dataset243-stage2-permission-v1"


class _ValidationLifecycle:
    """真实验收的正常路径不会触发生命周期收口。"""

    @staticmethod
    def cancel(*_args: Any, **_kwargs: Any) -> tuple[()]:
        return ()

    @staticmethod
    def suspend(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("STAGE2_VALIDATION_UNEXPECTED_SUSPEND")


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _time_bindings() -> tuple[ResearchTimeBinding, ResearchTimeBinding]:
    return (
        ResearchTimeBinding(
            role=ResearchTimeRole.CURRENT,
            expression="2026-06-29",
            dimension_ref=TIME_REF,
            normalized={
                "kind": "absolute_range",
                "start": "2026-06-29",
                "end_exclusive": "2026-06-30",
            },
        ),
        ResearchTimeBinding(
            role=ResearchTimeRole.PREVIOUS,
            expression="2026-06-28",
            dimension_ref=TIME_REF,
            normalized={
                "kind": "absolute_range",
                "start": "2026-06-28",
                "end_exclusive": "2026-06-29",
            },
        ),
    )


def _build_requirement(schema: Any, run_id: str) -> ResearchAgentRequirement:
    scope_payload = {
        "target_metric_refs": (GMV_REF,),
        "dimension_refs": (TIME_REF, SELLER_REF, STALL_REF),
        "driver_metric_refs": (ORDER_COUNT_REF, AOV_REF),
        "allowed_filter_refs": (TIME_REF, SELLER_REF, STALL_REF),
        "hierarchies": (
            ResearchHierarchy(
                hierarchy_id="seller_stall_hierarchy",
                dimension_refs=(SELLER_REF, STALL_REF),
            ),
        ),
        "driver_relationships": (
            ResearchDriverRelationship(
                target_metric_ref=GMV_REF,
                driver_metric_ref=ORDER_COUNT_REF,
                relationship_type="certified_driver",
                validation_method="SAME_DIRECTION",
                expected_direction="POSITIVE",
                dimension_refs=(SELLER_REF, STALL_REF),
                time_roles=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
                relationship_fingerprint="dataset243-order-count-driver-v2",
            ),
            ResearchDriverRelationship(
                target_metric_ref=GMV_REF,
                driver_metric_ref=AOV_REF,
                relationship_type="governed_analysis_relation",
                validation_method="SAME_DIRECTION",
                expected_direction="POSITIVE",
                dimension_refs=(SELLER_REF, STALL_REF),
                time_roles=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
                relationship_fingerprint="dataset243-aov-driver-v1",
            ),
        ),
        "contribution_metric_refs": (GMV_REF,),
        "contribution_dimension_refs": (SELLER_REF, STALL_REF),
        "contribution_tolerance": 1e-6,
        "tenant_scope": "tenant:1",
        "dataset_ref": "ASSET:dataset:243",
    }
    scope_fingerprint = _fingerprint(
        {
            **scope_payload,
            "schema_fingerprint": schema.schema_fingerprint,
            "permission_fingerprint": PERMISSION_FINGERPRINT,
        }
    )
    scope = ResearchScope.model_validate(
        {
            **scope_payload,
            "scope_fingerprint": scope_fingerprint,
        }
    )
    version = ResearchVersionSnapshot(
        schema_version=schema.schema_version,
        contract_version=schema.contract_version,
        schema_fingerprint=schema.schema_fingerprint,
        scope_fingerprint=scope_fingerprint,
        permission_fingerprint=PERMISSION_FINGERPRINT,
    )
    bindings = _time_bindings()
    return ResearchAgentRequirement(
        run_id=run_id,
        goal="验证 dataset 243 的比较、层级下钻、贡献度和驱动分析 Runtime",
        reason=ResearchReason.OPEN_ENDED_CAUSE,
        target_metric_refs=(GMV_REF,),
        time_bindings=bindings,
        time_bindings_by_model={str(MODEL_ID): bindings},
        scope=scope,
        evidence_requirements=(
            ResearchEvidenceRequirement(
                requirement_id="runtime-validation",
                kind="claim_support",
                description="每个真实 Runtime 用例必须返回当前 Run 的受治理 Evidence",
            ),
        ),
        version_snapshot=version,
        output_requirements=("所有结果必须来自 PROVEN 查询计划",),
    )


def _semantic_scope(schema: Any, requirement: ResearchAgentRequirement) -> SemanticAssetScope:
    return SemanticAssetScope(
        workspace_id=OID,
        user_id=USER_ID,
        datasource_id=DATASOURCE_ID,
        dataset_id=DATASET_ID,
        retrieval_id="dataset243-stage2-runtime",
        allowed_assets=(
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=271,
                model_id=MODEL_ID,
            ),
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=265,
                model_id=MODEL_ID,
            ),
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.METRIC,
                asset_id=303,
                model_id=MODEL_ID,
            ),
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.DIMENSION,
                asset_id=276,
                model_id=MODEL_ID,
            ),
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.DIMENSION,
                asset_id=277,
                model_id=MODEL_ID,
            ),
            ExecutableAssetReference(
                asset_type=RetrievalResourceType.DIMENSION,
                asset_id=278,
                model_id=MODEL_ID,
            ),
        ),
        schema_snapshot=schema,
        permission_version=PERMISSION_VERSION,
        scope_fingerprint=requirement.scope.scope_fingerprint,
        permission_fingerprint=PERMISSION_FINGERPRINT,
    )


def _create_state(session: Session, schema: Any) -> tuple[Any, Any, Any]:
    configure_agent_cleanup(AgentExecutionDeletionService)
    chat = build_chat_application_service(session).create(
        user_id=USER_ID,
        workspace_id=OID,
        request=CreateChat(
            dataset_id=DATASET_ID,
            datasource=DATASOURCE_ID,
            question="Semantic Query Runtime dataset 243 阶段 2 验收",
        ),
    )
    if chat.id is None:
        raise RuntimeError("STAGE2_VALIDATION_CHAT_ID_MISSING")
    record_service = build_chat_record_service(session)
    record = record_service.create(
        ChatRecordCreateData(
            chat_id=chat.id,
            user_id=USER_ID,
            question="执行无 ResearchAction 的真实 Semantic Query Runtime 验收",
            dataset_id=DATASET_ID,
            datasource_id=DATASOURCE_ID,
            engine_type=chat.engine_type,
            execution_type=ChatRecordExecutionType.AGENT,
        )
    )
    if record.id is None:
        raise RuntimeError("STAGE2_VALIDATION_RECORD_ID_MISSING")
    calendar = build_semantic_dataset_catalog_service(session).get_calendar(
        OID,
        DATASET_ID,
    )
    if calendar is None:
        raise RuntimeError("STAGE2_VALIDATION_CALENDAR_MISSING")
    temporal_context = build_dataset_temporal_context(
        default_timezone=calendar.default_timezone,
        calendar_type=calendar.calendar_type,
        week_start_day=calendar.week_start_day,
        fiscal_year_start_month=calendar.fiscal_year_start_month,
        holiday_calendar_key=calendar.holiday_calendar_key,
    )
    config = AgentConfig(
        enabled=True,
        execution_modes=("research",),
        plan_max_query_tasks=8,
        plan_query_concurrency=2,
        tool_timeout_seconds=120,
    )
    run = agent_run_repository.create_run(
        session,
        oid=OID,
        chat_id=chat.id,
        record_id=record.id,
        user_id=USER_ID,
        config=config.model_dump(mode="json"),
        temporal_context=temporal_context,
        execution_mode="research",
    )
    record_service.transition(
        record,
        ChatRecordStatus.RUNNING,
        run_id=str(run.id),
        execution_type=ChatRecordExecutionType.AGENT,
    )
    agent_run_repository.update_run(
        session,
        run,
        status=AgentRunStatus.RUNNING.value,
    )
    session.commit()
    result_artifact_service = build_result_artifact_service(session)
    result_store = ResultStore(result_artifact_service)
    state = AgentRuntimeStateFactory(
        session,
        USER_ID,
        config,
        AgentToolContextServices(
            result_artifact_service=result_artifact_service,
            result_store=result_store,
        ),
    ).create(run, record)
    requirement = _build_requirement(schema, f"research-runtime:{run.id}")
    semantic_scope = _semantic_scope(schema, requirement)
    state.context.permission_version = PERMISSION_VERSION
    state.context.state.update(
        {
            "execution_mode": "research",
            "research_run_id": requirement.run_id,
            "research_agent_requirement": requirement.model_dump(mode="json"),
            "semantic_scope": semantic_scope.model_dump(mode="json"),
        }
    )
    return state, requirement, record_service


def _build_runtime(session: Session) -> SemanticQueryRuntime:
    query_service = build_query_service(
        session,
        default_limit=1000,
        sample_rows=20,
        max_transient_retries=1,
    )
    registry = ToolRegistry(middlewares=default_middlewares())
    registry.register(
        CompileSemanticSqlTool(
            build_semantic_sql_compilation_service(session),
            query_service,
        )
    )
    registry.register(ValidateSqlTool(query_service))
    session_bind = session.get_bind()
    worker_engine = getattr(session_bind, "engine", session_bind)
    executor = QueryTaskExecutor(
        lambda: Session(worker_engine),
        lambda worker_session: build_query_service(
            worker_session,
            default_limit=1000,
            sample_rows=20,
            max_transient_retries=1,
        ),
    )
    execution_service = AnalysisExecutionService(
        AnalysisExecutionDependencies(
            registry=registry,
            result_processor=ChatBIToolResultProcessor(),
            lifecycle=_ValidationLifecycle(),  # type: ignore[arg-type]
            event_publisher=build_agent_event_publisher(session),
            session=session,
            query_task_executor=executor,
            max_query_tasks=8,
            query_concurrency=2,
            query_timeout_seconds=120,
            compute_engine=ComputeEngine(),
            semantic_schema_provider=build_semantic_schema_service(session),
        )
    )
    return SemanticQueryRuntime(execution_service)


def _query(
    requirement: ResearchAgentRequirement,
    *,
    metrics: tuple[str, ...],
    dimensions: tuple[str, ...] = (),
    comparison: ResearchQueryComparison = ResearchQueryComparison.DIFFERENCE,
    analysis: Literal[
        "compare",
        "breakdown",
        "drilldown",
        "filter_from_result",
        "contribution",
        "exploration",
    ] = "compare",
    purpose: str,
    evidence_value_filters: tuple[ResearchEvidenceValueRef, ...] = (),
    drilldown: ResearchDrilldownSpec | None = None,
) -> ResearchSemanticQuery:
    return ResearchSemanticQuery(
        run_id=requirement.run_id,
        scope_fingerprint=requirement.scope.scope_fingerprint,
        version_snapshot=requirement.version_snapshot,
        metrics=metrics,
        dimensions=dimensions,
        time_ranges=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
        evidence_value_filters=evidence_value_filters,
        comparison=comparison,
        analysis=analysis,
        drilldown=drilldown,
        limit=100,
        purpose=purpose,
    )


def _run_case(
    runtime: SemanticQueryRuntime,
    state: Any,
    requirement: ResearchAgentRequirement,
    name: str,
    query: ResearchSemanticQuery,
    *,
    evidence: tuple[ResearchEvidence, ...] = (),
    iteration: int,
) -> tuple[dict[str, Any], ResearchEvidence | None]:
    outcome = runtime.execute(
        state,
        query,
        requirement=requirement,
        evidence=evidence,
        plan_id=f"dataset243-stage2-{name}",
        iteration=iteration,
    )
    plan_payload = state.context.state.get("analysis_plan") or {}
    validation = plan_payload.get("validation") or {}
    result = {
        "name": name,
        "status": outcome.status,
        "plan_id": outcome.plan_id,
        "plan_status": validation.get("status"),
        "error_code": outcome.error_code.value if outcome.error_code else None,
        "failure_stage": (
            outcome.failure_stage.value if outcome.failure_stage else None
        ),
        "message": outcome.message,
        "result_ids": [item.result_id for item in outcome.result_refs],
        "evidence_ids": [item.evidence_id for item in outcome.evidence],
        "sample_rows": [
            row
            for item in outcome.evidence
            for row in item.sample_rows[:3]
        ],
    }
    if outcome.status != "succeeded":
        raise RuntimeError(json.dumps(result, ensure_ascii=False, default=str))
    if validation.get("status") != "PROVEN":
        raise RuntimeError(f"STAGE2_VALIDATION_PLAN_NOT_PROVEN:{name}")
    return result, outcome.evidence[0] if outcome.evidence else None


def main() -> int:
    with Session(engine) as session:
        schema = build_semantic_schema_service(session).build_dataset_schema(
            OID,
            DATASET_ID,
        )
        state, requirement, record_service = _create_state(session, schema)
        runtime = _build_runtime(session)
        results: list[dict[str, Any]] = []

        comparison, _ = _run_case(
            runtime,
            state,
            requirement,
            "comparison",
            _query(
                requirement,
                metrics=(GMV_REF,),
                purpose="比较 6 月 29 日和 6 月 28 日总 GMV",
            ),
            iteration=0,
        )
        results.append(comparison)

        seller_breakdown, seller_evidence = _run_case(
            runtime,
            state,
            requirement,
            "seller_breakdown",
            _query(
                requirement,
                metrics=(GMV_REF,),
                dimensions=(SELLER_REF,),
                analysis="breakdown",
                purpose="按商家比较总 GMV 变化",
            ),
            iteration=1,
        )
        results.append(seller_breakdown)
        if seller_evidence is None:
            raise RuntimeError("STAGE2_VALIDATION_SELLER_EVIDENCE_MISSING")

        seller_filter = ResearchEvidenceValueRef(
            run_id=requirement.run_id,
            evidence_id=seller_evidence.evidence_id,
            target_ref=SELLER_REF,
            column_ref=SELLER_REF,
            row_selector=ResearchRowSelector(
                rank=1,
                order_by=GMV_REF,
                direction=ResearchOrderDirection.ASC,
            ),
        )
        drilldown, _ = _run_case(
            runtime,
            state,
            requirement,
            "drilldown",
            _query(
                requirement,
                metrics=(GMV_REF,),
                dimensions=(STALL_REF,),
                analysis="drilldown",
                purpose="对下降最大的商家继续下钻到档口",
                evidence_value_filters=(seller_filter,),
                drilldown=ResearchDrilldownSpec(
                    hierarchy_id="seller_stall_hierarchy",
                    source_evidence_id=seller_evidence.evidence_id,
                    current_dimension_ref=SELLER_REF,
                    next_dimension_ref=STALL_REF,
                ),
            ),
            evidence=(seller_evidence,),
            iteration=2,
        )
        results.append(drilldown)

        contribution, _ = _run_case(
            runtime,
            state,
            requirement,
            "contribution",
            _query(
                requirement,
                metrics=(GMV_REF,),
                dimensions=(STALL_REF,),
                comparison=ResearchQueryComparison.CONTRIBUTION,
                analysis="contribution",
                purpose="计算各档口对总 GMV 变化的贡献并对账",
            ),
            iteration=3,
        )
        results.append(contribution)

        drivers, _ = _run_case(
            runtime,
            state,
            requirement,
            "driver_validation",
            _query(
                requirement,
                metrics=(ORDER_COUNT_REF, AOV_REF),
                purpose="比较总订单数和订单平均客单价的同期变化",
            ),
            iteration=4,
        )
        results.append(drivers)

        summary = {
            "dataset_id": DATASET_ID,
            "datasource_id": DATASOURCE_ID,
            "run_id": state.run.id,
            "research_run_id": requirement.run_id,
            "schema_version": schema.schema_version,
            "contract_version": schema.contract_version,
            "schema_fingerprint": schema.schema_fingerprint,
            "all_passed": all(item["status"] == "succeeded" for item in results),
            "cases": results,
        }
        state.context.state["stage2_runtime_validation"] = summary
        agent_run_repository.update_run(
            session,
            state.run,
            status=AgentRunStatus.FINISHED.value,
            derived_state=state.persistable_context(),
        )
        record_service.transition(
            state.record,
            ChatRecordStatus.SUCCEEDED,
            result=ChatRecordResultProjection(
                answer="Semantic Query Runtime dataset 243 阶段 2 验收通过",
                data=json.dumps(summary, ensure_ascii=False, default=str),
            ),
        )
        session.commit()
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
