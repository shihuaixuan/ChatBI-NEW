"""使用真实自然语言、模型和数据源验证第四阶段有限多步 Plan。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from run_plan_full_stage_cases import PlanCase, PlanCaseError, _run_case
from sqlmodel import Session

from apps.chatbi.adapters.prompts.limited_multistep import (
    DefaultLimitedMultiStepPromptBuilder,
)
from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.composition import build_query_service, build_semantic_parse_service
from apps.chatbi.models.dto.execution_requirement import ExecutionRequirement
from apps.chatbi.models.dto.research import (
    ResearchBreakdownAction,
    ResearchFilterFromResultAction,
    ResearchFocusedAnalysis,
    ResearchRowSelector,
)
from apps.chatbi.orchestration.pipeline.mode_router import ModeRouteInput, ModeRouter
from apps.chatbi.services.execution import QueryTaskExecutor
from apps.chatbi.services.planning.limited_multistep import LimitedMultiStepDecomposer
from apps.chatbi.services.research.actions import (
    initial_compare_action,
    materialize_research_action,
)
from apps.chatbi.services.research.evidence import project_evidence_snapshot
from apps.semantic.composition import (
    build_semantic_schema_service,
    build_semantic_sql_compilation_service,
)
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.temporal import build_temporal_context
from common.core.db import engine

DEFAULT_CASE_FILE = Path(__file__).parent / "data" / "plan_stage4_real_questions.json"


@dataclass(frozen=True, slots=True)
class Stage4QuestionCase:
    """真实问题集中的预期语义、路由和执行结果。"""

    name: str
    question: str
    metric_refs: tuple[str, ...]
    dimension_refs: tuple[str, ...]
    expected_multi_step: str | None
    expected_mode: str
    expected_query_count: int
    expected_operations: tuple[str, ...]
    execute: bool


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="验证真实问题经过 SemanticParse、受限分解、Plan 和 SQL 执行的流程。"
    )
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--dataset-id", type=int, required=True)
    parser.add_argument("--datasource-id", type=int, required=True)
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--reference-at", default="2026-08-20T12:00:00+08:00")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--sample-rows", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--task-timeout", type=float, default=60.0)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    return parser.parse_args()


def _load_cases(path: Path) -> tuple[Stage4QuestionCase, ...]:
    if not path.is_file():
        raise PlanCaseError(f"第四阶段问题集不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise PlanCaseError("第四阶段问题集必须是 JSON 数组")
    cases: list[Stage4QuestionCase] = []
    for item in payload:
        if not isinstance(item, dict):
            raise PlanCaseError("第四阶段问题项必须是 JSON 对象")
        cases.append(
            Stage4QuestionCase(
                name=str(item["name"]),
                question=str(item["question"]),
                metric_refs=tuple(str(ref) for ref in item["metric_refs"]),
                dimension_refs=tuple(
                    str(ref) for ref in item["dimension_refs"]
                ),
                expected_multi_step=(
                    str(item["expected_multi_step"])
                    if item.get("expected_multi_step") is not None
                    else None
                ),
                expected_mode=str(item["expected_mode"]),
                expected_query_count=int(item["expected_query_count"]),
                expected_operations=tuple(
                    str(operation) for operation in item["expected_operations"]
                ),
                execute=bool(item["execute"]),
            )
        )
    return tuple(cases)


def _schema_candidates(
    schema: DatasetSchema,
    case: Stage4QuestionCase,
) -> dict[str, Any]:
    elements = {
        **{f"METRIC:{item.id}:{item.model}": item for item in schema.metrics},
        **{
            f"DIMENSION:{item.id}:{item.model}": item
            for item in schema.dimensions
        },
    }

    def candidate(ref: str, expected_type: str) -> dict[str, Any]:
        element = elements.get(ref)
        if element is None or element.type != expected_type:
            raise PlanCaseError(f"第四阶段问题资产不存在：{ref}")
        return _candidate_payload(ref, element)

    return {
        "candidate_groups": {
            "metrics": [candidate(ref, "METRIC") for ref in case.metric_refs],
            "dimensions": [
                candidate(ref, "DIMENSION") for ref in case.dimension_refs
            ],
        }
    }


def _candidate_payload(ref: str, element: SchemaElement) -> dict[str, Any]:
    return {
        "ref": ref,
        "asset_type": element.type,
        "asset_id": element.id,
        "model_id": element.model,
        "display_name": element.name,
        "biz_name": element.biz_name,
        "description": str(element.description or ""),
        "matched_phrases": [element.name],
        "score": 1.0,
    }


def _print_stage(case: Stage4QuestionCase, stage: str, payload: Any) -> None:
    print(f"\n--- [{case.name}] {stage} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    args = _parse_args()
    cases = _load_cases(args.case_file)
    if args.list_cases:
        for case in cases:
            print(f"{case.name}: {case.question}")
        return 0
    selected = set(args.case)
    cases = tuple(case for case in cases if not selected or case.name in selected)
    if not cases:
        raise PlanCaseError("没有选中第四阶段问题用例")
    reference_at = datetime.fromisoformat(args.reference_at)
    temporal_context = build_temporal_context(
        reference_at=reference_at,
        timezone=args.timezone,
    )

    model_service = build_question_model_service(enforce_json=True, max_retries=0)
    semantic_parse_service = build_semantic_parse_service(model_service)
    decomposer = LimitedMultiStepDecomposer(
        model_service,
        DefaultLimitedMultiStepPromptBuilder(),
    )
    with Session(engine) as session:
        schema_provider = build_semantic_schema_service(session)
        schema = schema_provider.build_dataset_schema(args.tenant_id, args.dataset_id)
        router = ModeRouter(schema_provider, decomposer)
        sql_compilation_service = build_semantic_sql_compilation_service(session)
        query_service = build_query_service(
            session,
            default_limit=1000,
            sample_rows=max(args.sample_rows, 10),
        )
        query_task_executor = QueryTaskExecutor(
            lambda: Session(engine),
            lambda worker_session: build_query_service(
                worker_session,
                default_limit=1000,
                sample_rows=max(args.sample_rows, 10),
            ),
        )
        for case in cases:
            print(f"\n{'=' * 80}\n真实问题：{case.question}\n{'=' * 80}")
            candidates = _schema_candidates(schema, case)
            semantic_parse = semantic_parse_service.parse(
                rewrite_question=case.question,
                candidate_payload=candidates,
            )
            _print_stage(
                case,
                "1. SemanticParseOutput",
                semantic_parse.model_dump(mode="json"),
            )
            actual_multi_step = (
                semantic_parse.multi_step.type
                if semantic_parse.multi_step is not None
                else None
            )
            if actual_multi_step != case.expected_multi_step:
                raise PlanCaseError(
                    "STAGE4_SEMANTIC_MULTI_STEP_MISMATCH:"
                    f"{case.expected_multi_step}:{actual_multi_step}"
                )
            route_payload = router.route(
                ModeRouteInput(
                    semantic_parse=semantic_parse,
                    candidate_groups=candidates["candidate_groups"],
                    dataset_id=args.dataset_id,
                    tenant_id=args.tenant_id,
                    enabled_modes=("fast", "plan", "research"),
                    temporal_context=temporal_context,
                    datasource_id=args.datasource_id,
                )
            )
            requirement = ExecutionRequirement.model_validate(route_payload)
            _print_stage(
                case,
                "2. ExecutionRequirement",
                requirement.model_dump(mode="json"),
            )
            operations = tuple(
                item.type.value for item in requirement.post_calculations
            )
            if requirement.route.mode != case.expected_mode:
                raise PlanCaseError("STAGE4_ROUTE_MISMATCH")
            if len(requirement.query_requirements) != case.expected_query_count:
                raise PlanCaseError("STAGE4_QUERY_COUNT_MISMATCH")
            if operations != case.expected_operations:
                raise PlanCaseError(
                    f"STAGE4_OPERATIONS_MISMATCH:{case.expected_operations}:{operations}"
                )
            if case.expected_multi_step == "limited_multistep":
                if requirement.decomposition is None:
                    raise PlanCaseError("STAGE4_DECOMPOSITION_AUDIT_REQUIRED")
            elif requirement.decomposition is not None:
                raise PlanCaseError("STAGE4_RULE_CASE_CALLED_DECOMPOSER")
            if case.expected_mode == "research":
                if requirement.research_requirement is None:
                    raise PlanCaseError("RESEARCH_REQUIREMENT_REQUIRED")
                _print_stage(
                    case,
                    "3. ResearchRequirement",
                    requirement.research_requirement.model_dump(mode="json"),
                )
                if case.execute:
                    research = requirement.research_requirement
                    initial = materialize_research_action(
                        action=initial_compare_action(research),
                        research_requirement=research,
                        runtime=requirement.runtime,
                        asset_snapshot=requirement.asset_snapshot,
                        evidence_by_result={},
                    )
                    initial_result = _run_case(
                        PlanCase(
                            name="research_premise",
                            description="Research 规则生成的现象确认",
                            semantic_parse=semantic_parse.model_dump(mode="json"),
                            expected_query_count=len(
                                initial.requirement.query_requirements
                            ),
                            expected_operations=tuple(
                                item.type.value
                                for item in initial.requirement.post_calculations
                            ),
                            expected_route="plan",
                        ),
                        args,
                        schema_provider=schema_provider,
                        sql_compilation_service=sql_compilation_service,
                        query_service=query_service,
                        query_task_executor=query_task_executor,
                        schema=schema,
                        temporal_context=temporal_context,
                        requirement_override=initial.requirement,
                    )
                    if args.plan_only:
                        print(f"\n[{case.name}] Research 初始子计划证明通过。")
                        continue
                    if initial_result is None:
                        raise PlanCaseError("RESEARCH_INITIAL_RESULT_REQUIRED")
                    initial_evidence = project_evidence_snapshot(
                        materialized=initial,
                        plan_id=initial_result.ref.plan_id,
                        task_id=initial_result.ref.node_id,
                        result_id=initial_result.ref.result_set_id,
                        rows=[dict(row) for row in initial_result.rows],
                        fields=list(initial_result.ref.fields),
                        evidence_index=1,
                        max_rows=research.budget.max_evidence_rows,
                        max_chars=research.budget.max_evidence_chars,
                    )
                    _print_stage(
                        case,
                        "4. 现象确认 EvidenceSnapshot",
                        initial_evidence.model_dump(mode="json"),
                    )

                    target_dimension_ref = case.dimension_refs[0]
                    breakdown_action = ResearchBreakdownAction(
                        metric_ref=research.target_metric_refs[0],
                        dimension_ref=target_dimension_ref,
                        calculation="difference",
                        time_roles=("current", "previous"),
                    )
                    breakdown = materialize_research_action(
                        action=breakdown_action,
                        research_requirement=research,
                        runtime=requirement.runtime,
                        asset_snapshot=requirement.asset_snapshot,
                        evidence_by_result={
                            initial_evidence.result_id: initial_evidence
                        },
                    )
                    breakdown_result = _run_case(
                        PlanCase(
                            name="research_breakdown",
                            description="Research 按用户目标维度拆分变化",
                            semantic_parse=semantic_parse.model_dump(mode="json"),
                            expected_query_count=2,
                            expected_operations=("difference",),
                            expected_route="plan",
                        ),
                        args,
                        schema_provider=schema_provider,
                        sql_compilation_service=sql_compilation_service,
                        query_service=query_service,
                        query_task_executor=query_task_executor,
                        schema=schema,
                        temporal_context=temporal_context,
                        requirement_override=breakdown.requirement,
                    )
                    if breakdown_result is None:
                        raise PlanCaseError("RESEARCH_BREAKDOWN_RESULT_REQUIRED")
                    breakdown_evidence = project_evidence_snapshot(
                        materialized=breakdown,
                        plan_id=breakdown_result.ref.plan_id,
                        task_id=breakdown_result.ref.node_id,
                        result_id=breakdown_result.ref.result_set_id,
                        rows=[dict(row) for row in breakdown_result.rows],
                        fields=list(breakdown_result.ref.fields),
                        evidence_index=2,
                        max_rows=research.budget.max_evidence_rows,
                        max_chars=research.budget.max_evidence_chars,
                    )
                    _print_stage(
                        case,
                        "5. Breakdown EvidenceSnapshot",
                        breakdown_evidence.model_dump(mode="json"),
                    )
                    next_dimension_ref = next(
                        (
                            ref
                            for ref in research.scope.dimension_refs
                            if ref != target_dimension_ref
                        ),
                        None,
                    )
                    focused = ResearchFocusedAnalysis(
                        type="breakdown" if next_dimension_ref else "compare",
                        metric_refs=research.target_metric_refs,
                        time_roles=("current", "previous"),
                        dimension_ref=next_dimension_ref,
                    )
                    follow_up_action = ResearchFilterFromResultAction(
                        source_result_id=breakdown_evidence.result_id,
                        row_selector=ResearchRowSelector(
                            rank=1,
                            order_by=breakdown_evidence.row_order,
                            direction="asc",
                        ),
                        target_dimension_ref=target_dimension_ref,
                        analysis=focused,
                    )
                    follow_up = materialize_research_action(
                        action=follow_up_action,
                        research_requirement=research,
                        runtime=requirement.runtime,
                        asset_snapshot=requirement.asset_snapshot,
                        evidence_by_result={
                            breakdown_evidence.result_id: breakdown_evidence
                        },
                    )
                    _run_case(
                        PlanCase(
                            name="research_filter_follow_up",
                            description="从 Breakdown 结果选择下降最大对象继续分析",
                            semantic_parse=semantic_parse.model_dump(mode="json"),
                            expected_query_count=2,
                            expected_operations=("difference",),
                            expected_route="plan",
                        ),
                        args,
                        schema_provider=schema_provider,
                        sql_compilation_service=sql_compilation_service,
                        query_service=query_service,
                        query_task_executor=query_task_executor,
                        schema=schema,
                        temporal_context=temporal_context,
                        requirement_override=follow_up.requirement,
                    )
                    print(f"\n[{case.name}] Research 第二阶段真实动态链路通过。")
                    continue
            elif requirement.research_requirement is not None:
                raise PlanCaseError("RESEARCH_REQUIREMENT_MODE_MISMATCH")
            if not case.execute:
                print(f"\n[{case.name}] 语义与路由边界验证通过。")
                continue
            executable_case = PlanCase(
                name=case.name,
                description=case.question,
                semantic_parse=semantic_parse.model_dump(mode="json"),
                expected_query_count=case.expected_query_count,
                expected_operations=case.expected_operations,
                expected_route=case.expected_mode,
            )
            _run_case(
                executable_case,
                args,
                schema_provider=schema_provider,
                sql_compilation_service=sql_compilation_service,
                query_service=query_service,
                query_task_executor=query_task_executor,
                schema=schema,
                temporal_context=temporal_context,
                requirement_override=requirement,
            )

    print(f"\n全部通过：共完成 {len(cases)} 个第四阶段真实问题用例。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PlanCaseError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"测试失败：{exc}") from exc
