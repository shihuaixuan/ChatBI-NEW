from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.chatbi.models.dto.research_agent import (
    ResearchAgentReport,
    ResearchAgentRequirement,
    ResearchCompletion,
    ResearchComputeOperation,
    ResearchComputeRequest,
    ResearchDriverRelationship,
    ResearchEvidence,
    ResearchEvidenceCitation,
    ResearchEvidenceRequirement,
    ResearchEvidenceStatistics,
    ResearchEvidenceValueRef,
    ResearchExecutionMode,
    ResearchFinishRequest,
    ResearchHierarchy,
    ResearchHypothesisAssessment,
    ResearchInspectEvidenceRequest,
    ResearchLiteralFilter,
    ResearchLogicalColumn,
    ResearchResultRef,
    ResearchRowSelector,
    ResearchScope,
    ResearchSemanticQuery,
    ResearchTimeBinding,
    ResearchTimeRole,
    ResearchToolCall,
    ResearchToolCallRef,
    ResearchVersionSnapshot,
    ResearchWorkingState,
    ToolErrorCode,
    ToolFailureStage,
    ToolObservation,
)


def _version() -> dict[str, object]:
    return {
        "schema_version": 22,
        "contract_version": 3,
        "schema_fingerprint": "schema-1",
        "scope_fingerprint": "scope-1",
        "permission_fingerprint": "permission-1",
    }


def _scope() -> dict[str, object]:
    return {
        "target_metric_refs": ["METRIC:10:1"],
        "dimension_refs": ["DIMENSION:20:1"],
        "driver_metric_refs": ["METRIC:11:1"],
        "allowed_filter_refs": ["DIMENSION:20:1"],
        "tenant_scope": "tenant-1",
        "dataset_ref": "ASSET:dataset:1",
        "scope_fingerprint": "scope-1",
    }


def _governed_scope() -> ResearchScope:
    """构造包含层级、驱动关系和贡献度范围的最小治理 Scope。"""

    return ResearchScope(
        target_metric_refs=("METRIC:10:1",),
        dimension_refs=(
            "DIMENSION:20:1",
            "DIMENSION:21:1",
            "DIMENSION:22:1",
        ),
        driver_metric_refs=("METRIC:11:1",),
        allowed_filter_refs=(
            "DIMENSION:20:1",
            "DIMENSION:21:1",
            "DIMENSION:22:1",
        ),
        hierarchies=(
            ResearchHierarchy(
                hierarchy_id="geo",
                dimension_refs=(
                    "DIMENSION:20:1",
                    "DIMENSION:21:1",
                    "DIMENSION:22:1",
                ),
            ),
        ),
        driver_relationships=(
            ResearchDriverRelationship(
                target_metric_ref="METRIC:10:1",
                driver_metric_ref="METRIC:11:1",
                relationship_type="certified_driver",
                dimension_refs=("DIMENSION:20:1",),
                time_roles=("current", "previous"),
                relationship_fingerprint="relation-1",
            ),
        ),
        contribution_metric_refs=("METRIC:10:1",),
        contribution_dimension_refs=("DIMENSION:21:1",),
        tenant_scope="tenant-1",
        dataset_ref="ASSET:dataset:1",
        scope_fingerprint="scope-1",
    )


def _governed_requirement() -> ResearchAgentRequirement:
    """构造使用治理 Scope 的双时间角色 Requirement。"""

    return ResearchAgentRequirement(
        run_id="run-1",
        goal="分析总 GMV 变化",
        reason="open_ended_cause",
        target_metric_refs=("METRIC:10:1",),
        time_bindings=(
            ResearchTimeBinding(
                role="current",
                expression="本月",
                dimension_ref="DIMENSION:20:1",
                normalized={"kind": "absolute_range", "start": "2026-08-01"},
            ),
            ResearchTimeBinding(
                role="previous",
                expression="上月",
                dimension_ref="DIMENSION:20:1",
                normalized={"kind": "absolute_range", "start": "2026-07-01"},
            ),
        ),
        scope=_governed_scope(),
        evidence_requirements=(
            ResearchEvidenceRequirement(
                requirement_id="premise",
                kind="premise_confirmation",
                description="确认变化事实",
            ),
        ),
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
    )


def _requirement() -> ResearchAgentRequirement:
    return ResearchAgentRequirement(
        run_id="run-1",
        goal="分析总 GMV 变化",
        reason="open_ended_cause",
        target_metric_refs=("METRIC:10:1",),
        time_bindings=(
            ResearchTimeBinding(
                role="current",
                expression="本月",
                dimension_ref="DIMENSION:20:1",
                normalized={"kind": "absolute_range", "start": "2026-08-01"},
            ),
        ),
        scope=ResearchScope.model_validate(_scope()),
        evidence_requirements=(
            ResearchEvidenceRequirement(
                requirement_id="premise",
                kind="premise_confirmation",
                description="确认变化事实",
            ),
            ResearchEvidenceRequirement(
                requirement_id="claim",
                kind="claim_support",
                description="支持最终结论",
            ),
        ),
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
    )


def _query() -> ResearchSemanticQuery:
    return ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
        metrics=("METRIC:10:1",),
        dimensions=("DIMENSION:20:1",),
        time_ranges=("current",),
        purpose="确认当前期变化",
    )


def _evidence(iteration: int = 1) -> ResearchEvidence:
    return ResearchEvidence(
        run_id="run-1",
        evidence_id="evidence-1",
        source_tool_call=ResearchToolCallRef(
            run_id="run-1", tool_call_id="tool-call-1"
        ),
        result_ref=ResearchResultRef(run_id="run-1", result_id="result-1"),
        iteration=iteration,
        purpose="确认当前期变化",
        metric_refs=("METRIC:10:1",),
        dimension_refs=("DIMENSION:20:1",),
        time_ranges=("current",),
        logical_columns=(
            ResearchLogicalColumn(asset_ref="METRIC:10:1", value_role="value"),
            ResearchLogicalColumn(
                asset_ref="DIMENSION:20:1", value_role="group_key"
            ),
        ),
        statistics=ResearchEvidenceStatistics(row_count=1),
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
    )


def test_core_contracts_round_trip_without_action_types() -> None:
    requirement = _requirement()
    query = _query()
    observation = ToolObservation(
        run_id="run-1",
        tool_call_id="tool-call-1",
        tool_name="query_semantic_data",
        status="succeeded",
        result_ids=("result-1",),
    )
    evidence = _evidence()
    state = ResearchWorkingState(
        run_id="run-1",
        goal="分析变化",
        target_metric_refs=("METRIC:10:1",),
        time_bindings=(
            ResearchTimeBinding(
                role="current",
                expression="本月",
                dimension_ref="DIMENSION:20:1",
                normalized={"kind": "absolute_range"},
            ),
        ),
        scope_fingerprint="scope-1",
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
        budget_remaining={
            "iterations": 2,
            "queries": 4,
            "model_calls": 4,
            "duration_seconds": 100,
        },
    )
    assert ResearchAgentRequirement.model_validate(requirement.model_dump()) == requirement
    assert ResearchSemanticQuery.model_validate(query.model_dump()) == query
    assert ToolObservation.model_validate(observation.model_dump()) == observation
    assert ResearchEvidence.model_validate(evidence.model_dump()) == evidence
    assert ResearchWorkingState.model_validate(state.model_dump()) == state
    assert "allowed_actions" not in requirement.model_dump()
    assert query.agent_contract_version == 1
    assert query.version_snapshot.schema_version == 22
    assert query.version_snapshot.contract_version == 3
    assert "agent_contract_version" not in query.version_snapshot.model_dump()


def test_new_contracts_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchSemanticQuery.model_validate({**_query().model_dump(), "sql": "SELECT 1"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchRowSelector.model_validate({"rank": 1, "value": "not allowed"})


def test_only_top_level_contracts_carry_agent_protocol_version() -> None:
    query = _query()
    assert query.agent_contract_version == 1
    assert ResearchComputeRequest(
        run_id="run-1",
        operation="difference",
        input_evidence_ids=("evidence-1",),
    ).agent_contract_version == 1
    assert "agent_contract_version" not in query.version_snapshot.model_dump()
    assert "agent_contract_version" not in ResearchTimeBinding(
        role="current",
        expression="本月",
        dimension_ref="DIMENSION:20:1",
        normalized={"kind": "absolute_range"},
    ).model_dump()


def test_requirement_rejects_scope_outside_metric_and_filter() -> None:
    with pytest.raises(ValueError, match="TARGET_METRIC_OUT_OF_SCOPE"):
        ResearchAgentRequirement(
            **{
                **_requirement().model_dump(),
                "target_metric_refs": ("METRIC:999:1",),
            }
        )
    with pytest.raises(ValueError, match="IMMUTABLE_FILTER_OUT_OF_SCOPE"):
        ResearchAgentRequirement(
            **{
                **_requirement().model_dump(),
                "immutable_filters": (
                    {"target_ref": "DIMENSION:999:1", "operator": "equals", "value": "x"},
                ),
            }
        )
    with pytest.raises(ValueError, match="IMMUTABLE_FILTER_DUPLICATED"):
        ResearchAgentRequirement(
            **{
                **_requirement().model_dump(),
                "immutable_filters": (
                    {"target_ref": "DIMENSION:20:1", "operator": "equals", "value": "x"},
                    {"target_ref": "DIMENSION:20:1", "operator": "equals", "value": "y"},
                ),
            }
        )
    with pytest.raises(ValueError, match="TIME_DIMENSION_OUT_OF_SCOPE"):
        ResearchAgentRequirement(
            **{
                **_requirement().model_dump(),
                "time_bindings": (
                    {
                        "role": "current",
                        "expression": "本月",
                        "dimension_ref": "DIMENSION:999:1",
                        "normalized": {"kind": "absolute_range"},
                    },
                ),
            }
        )


def test_query_rejects_out_of_scope_and_physical_payload() -> None:
    requirement = _requirement()
    with pytest.raises(ValueError, match="QUERY_DIMENSION_OUT_OF_SCOPE"):
        requirement.validate_query(
            ResearchSemanticQuery(
                **{
                    **_query().model_dump(),
                    "dimensions": ("DIMENSION:999:1",),
                }
            )
        )
    with pytest.raises(ValueError, match="PHYSICAL_PAYLOAD_FORBIDDEN"):
        ResearchLiteralFilter(
            target_ref="DIMENSION:20:1",
            operator="equals",
            value={"physical_column": "customer_id"},
        )


def test_query_cannot_change_frozen_time_or_immutable_filter() -> None:
    payload = _requirement().model_dump()
    payload["immutable_filters"] = [
        {"target_ref": "DIMENSION:20:1", "operator": "equals", "value": "华东"}
    ]
    requirement = ResearchAgentRequirement.model_validate(payload)
    with pytest.raises(ValueError, match="IMMUTABLE_FILTER_MISSING"):
        requirement.validate_query(_query())
    changed = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "filters": [
                {
                    "target_ref": "DIMENSION:20:1",
                    "operator": "equals",
                    "value": "华南",
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="IMMUTABLE_FILTER_CHANGED"):
        requirement.validate_query(changed)

    time_changed = ResearchSemanticQuery(
        **{**_query().model_dump(), "time_ranges": ("previous",)}
    )
    with pytest.raises(ValueError, match="TIME_BINDING_CHANGED"):
        _requirement().validate_query(time_changed)


def test_value_ref_requires_current_run_and_existing_logical_column() -> None:
    evidence = _evidence()
    value_ref = ResearchEvidenceValueRef(
        run_id="run-1",
        evidence_id="evidence-1",
        target_ref="DIMENSION:20:1",
        column_ref="METRIC:10:1",
        row_selector=ResearchRowSelector(rank=1),
    )
    value_ref.validate_against(evidence)
    with pytest.raises(ValueError, match="CROSS_RUN"):
        ResearchEvidenceValueRef(
            **{**value_ref.model_dump(), "run_id": "run-2"}
        ).validate_against(evidence)
    with pytest.raises(ValueError, match="COLUMN_NOT_FOUND"):
        ResearchEvidenceValueRef(
            **{**value_ref.model_dump(), "column_ref": "METRIC:999:1"}
        ).validate_against(evidence)


def test_query_rejects_cross_run_or_unknown_evidence_value() -> None:
    query = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "evidence_value_filters": [
                {
                    "run_id": "run-2",
                    "evidence_id": "evidence-1",
                    "target_ref": "DIMENSION:20:1",
                    "column_ref": "DIMENSION:20:1",
                    "row_selector": {"rank": 1},
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="CROSS_RUN"):
        query.validate_scope(_requirement().scope, (_evidence(),))


def test_drilldown_requires_current_run_source_evidence() -> None:
    query = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "analysis": "drilldown",
            "dimensions": ("DIMENSION:21:1",),
            "drilldown": {
                "hierarchy_id": "geo",
                "source_evidence_id": "missing-evidence",
                "current_dimension_ref": "DIMENSION:20:1",
                "next_dimension_ref": "DIMENSION:21:1",
            },
        }
    )
    with pytest.raises(ValueError, match="EVIDENCE_NOT_FOUND"):
        _governed_requirement().validate_query(query)

    cross_run_evidence = ResearchEvidence(
        **{
            **_evidence().model_dump(),
            "run_id": "run-2",
            "source_tool_call": {"run_id": "run-2", "tool_call_id": "tool-call-2"},
            "result_ref": {"run_id": "run-2", "result_id": "result-2"},
        }
    )
    cross_run_query = ResearchSemanticQuery(
        **{
            **query.model_dump(),
            "drilldown": {
                **query.drilldown.model_dump(),
                "source_evidence_id": "evidence-1",
            },
        }
    )
    with pytest.raises(ValueError, match="EVIDENCE_CROSS_RUN"):
        _governed_requirement().validate_query(cross_run_query, (cross_run_evidence,))


def test_evidence_rejects_cross_run_and_same_round_dependencies() -> None:
    base = _evidence()
    with pytest.raises(ValueError, match="TOOL_CALL_CROSS_RUN"):
        ResearchEvidence(
            **{**base.model_dump(), "source_tool_call": {"run_id": "run-2", "tool_call_id": "t"}}
        )
    with pytest.raises(ValueError, match="DEPENDENCY_MUST_PRECEDE"):
        ResearchEvidence(
            **{
                **base.model_dump(),
                "dependencies": [
                    {
                        "evidence_id": "evidence-0",
                        "run_id": "run-1",
                        "source_iteration": 1,
                        "relation": "supports",
                    }
                ],
            }
        )


def test_observation_success_and_failure_error_contract() -> None:
    success = ToolObservation(
        run_id="run-1",
        tool_call_id="tool-call-1",
        tool_name="query_semantic_data",
        status="succeeded",
        result_ids=("result-1",),
    )
    assert success.error_code is None
    with pytest.raises(ValueError, match="SUCCESS_OBSERVATION_ERROR_FORBIDDEN"):
        ToolObservation(
            **{
                **success.model_dump(),
                "error_code": ToolErrorCode.EXECUTION_FAILED,
            }
        )
    with pytest.raises(ValueError, match="FAILURE_ERROR_CODE_REQUIRED"):
        ToolObservation(
            run_id="run-1",
            tool_call_id="tool-call-1",
            tool_name="query_semantic_data",
            status="failed",
            failure_stage="execution",
            error_category="execution",
            message="timeout",
            details={"can_retry": True},
        )
    with pytest.raises(ValueError, match="FAILURE_DETAILS_REQUIRED"):
        ToolObservation(
            run_id="run-1",
            tool_call_id="tool-call-1",
            tool_name="query_semantic_data",
            status="failed",
            failure_stage=ToolFailureStage.EXECUTION,
            error_code=ToolErrorCode.EXECUTION_FAILED,
            error_category="execution",
            message="failed",
        )


def test_working_state_rejects_full_results_and_frozen_what_changes() -> None:
    state = ResearchWorkingState(
        run_id="run-1",
        goal="分析变化",
        target_metric_refs=("METRIC:10:1",),
        time_bindings=(
            ResearchTimeBinding(
                role="current",
                expression="本月",
                dimension_ref="DIMENSION:20:1",
                normalized={"kind": "absolute_range"},
            ),
        ),
        scope_fingerprint="scope-1",
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
        budget_remaining={"iterations": 2, "queries": 4, "model_calls": 4, "duration_seconds": 100},
    )
    with pytest.raises(ValueError, match="FROZEN_WHAT_CHANGED"):
        state.evolve(target_metric_refs=("METRIC:11:1",))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchWorkingState.model_validate(
            {**state.model_dump(), "full_results": [{"value": 1}]}
        )


def test_completion_and_report_require_evidence_citations() -> None:
    claim = {"statement": "指标下降", "evidence_ids": ["evidence-1"]}
    completion = ResearchCompletion(
        run_id="run-1",
        status="succeeded",
        reason="sufficient_evidence",
        summary="已完成",
        claims=(claim,),
        evidence_ids=("evidence-1",),
    )
    report = ResearchAgentReport(
        run_id="run-1",
        goal="分析变化",
        summary="完成",
        completion=completion,
        findings=(claim,),
        citations=(
            ResearchEvidenceCitation(
                evidence_id="evidence-1", run_id="run-1", purpose="支持结论"
            ),
        ),
    )
    assert ResearchAgentReport.model_validate(report.model_dump()) == report
    with pytest.raises(ValueError, match="FINDING_CITATION_MISSING"):
        ResearchAgentReport(
            run_id="run-1",
            goal="分析变化",
            summary="完成",
            completion=completion,
            findings=(claim,),
        )
    with pytest.raises(ValueError, match="COMPLETION_EVIDENCE_REQUIRED"):
        ResearchCompletion(
            run_id="run-1",
            status="succeeded",
            reason="sufficient_evidence",
            summary="没有证据的成功结论",
        )


def test_tool_call_rejects_physical_argument() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchToolCall(
            run_id="run-1",
            tool_call_id="tool-call-1",
            tool_name="query_semantic_data",
            iteration=0,
            arguments={"table": "orders"},
            request_fingerprint="fingerprint-1",
        )


def test_tool_args_are_independent_strict_dtos() -> None:
    operations = tuple(ResearchComputeOperation)
    computes = {
        operation: ResearchComputeRequest(
            run_id="run-1",
            operation=operation,
            input_evidence_ids=("evidence-1", "evidence-2"),
            metric_refs=("METRIC:10:1",),
            dimension_refs=("DIMENSION:20:1",),
            group_by_refs=("DIMENSION:20:1",),
            order=({"ref": "METRIC:10:1"},),
            limit=5,
            tolerance=0.01,
        )
        for operation in operations
    }
    inspect = ResearchInspectEvidenceRequest(
        run_id="run-1",
        evidence_id="evidence-1",
        logical_column_refs=("DIMENSION:20:1",),
        order=({"ref": "METRIC:10:1"},),
        offset=10,
        limit=5,
        max_rows=5,
        max_chars=500,
    )
    finish = ResearchFinishRequest(
        run_id="run-1",
        reason="data_insufficient",
        summary="数据不足",
        hypothesis_assessments=(
            ResearchHypothesisAssessment(
                hypothesis_id="hypothesis-1",
                assessment="inconclusive",
                evidence_ids=("evidence-1",),
                reason="样本不足",
            ),
        ),
        evidence_ids=("evidence-1",),
        unanswered_questions=("是否需要更长时间范围？",),
    )
    assert set(computes) == set(ResearchComputeOperation)
    assert computes[ResearchComputeOperation.TOP_N_OTHER].limit == 5
    assert inspect.max_rows == 5
    assert finish.reason.value == "data_insufficient"

    with pytest.raises(ValidationError, match="Input should be 'difference'"):
        ResearchComputeRequest(
            run_id="run-1",
            operation="unknown",
            input_evidence_ids=("evidence-1",),
        )
    with pytest.raises(ValueError, match="TOP_N_LIMIT_REQUIRED"):
        ResearchComputeRequest(
            run_id="run-1",
            operation="top_n_other",
            input_evidence_ids=("evidence-1",),
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchInspectEvidenceRequest(
            run_id="run-1",
            evidence_id="evidence-1",
            options={"limit": 5},
        )


def test_agent_protocol_version_is_separate_from_published_versions() -> None:
    with pytest.raises(ValidationError, match="agent_contract_version"):
        ResearchSemanticQuery.model_validate(
            {**_query().model_dump(), "agent_contract_version": 2}
        )
    assert ResearchTimeRole.CURRENT.value == "current"
    with pytest.raises(ValidationError):
        ResearchTimeBinding(
            role="future",
            expression="未来",
            dimension_ref="DIMENSION:20:1",
            normalized={"kind": "absolute_range"},
        )


def test_scope_rejects_governance_references_outside_scope() -> None:
    base = _governed_scope().model_dump()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchScope(**{**base, "hierarchy_ids": ("geo",)})
    with pytest.raises(ValueError, match="DRIVER_METRIC_OUT_OF_SCOPE"):
        ResearchScope(
            **{
                **base,
                "driver_relationships": [
                    {
                        **base["driver_relationships"][0],
                        "driver_metric_ref": "METRIC:999:1",
                    }
                ],
            }
        )
    with pytest.raises(ValueError, match="CONTRIBUTION_DIMENSION_OUT_OF_SCOPE"):
        ResearchScope(
            **{
                **base,
                "contribution_dimension_refs": ("DIMENSION:999:1",),
            }
        )
    with pytest.raises(ValueError, match="EXCLUDED_ASSET_INCLUDED"):
        ResearchScope(
            **{
                **base,
                "excluded_asset_refs": ("METRIC:10:1",),
            }
        )


def test_driver_relationship_preserves_formula_and_cross_model_invariants() -> None:
    with pytest.raises(ValueError, match="FORMULA_COMPONENT_REQUIRED"):
        ResearchDriverRelationship(
            target_metric_ref="METRIC:10:1",
            driver_metric_ref="METRIC:11:1",
            relationship_type="formula_component",
            time_roles=("current",),
            relationship_fingerprint="formula-1",
        )
    with pytest.raises(ValueError, match="CROSS_MODEL_RELATION_PATH_REQUIRED"):
        ResearchDriverRelationship(
            target_metric_ref="METRIC:10:1",
            driver_metric_ref="METRIC:11:2",
            relationship_type="certified_driver",
            time_roles=("current",),
            relationship_fingerprint="cross-model-1",
        )


def test_driver_relationship_model_id_is_third_ref_segment() -> None:
    """引用格式 ``KIND:资产ID:模型ID``：同模型不同资产不是跨模型（run 1264 回归）。

    生产冻结引用由 requirements.py 以 ``f"METRIC:{id}:{model}"`` 构造，
    模型 ID 在第三段；读错段会把单模型数据集的全部 driver 关系误判为跨模型。
    """

    relationship = ResearchDriverRelationship(
        target_metric_ref="METRIC:271:246",
        driver_metric_ref="METRIC:265:246",
        relationship_type="certified_driver",
        dimension_refs_by_model={"246": ("DIMENSION:276:246",)},
        time_roles=("current",),
        relationship_fingerprint="same-model-different-assets",
    )
    assert relationship.relation_path == ()
    with pytest.raises(ValueError, match="CROSS_MODEL_RELATION_PATH_REQUIRED"):
        ResearchDriverRelationship(
            target_metric_ref="METRIC:271:246",
            driver_metric_ref="METRIC:265:13",
            relationship_type="certified_driver",
            time_roles=("current",),
            relationship_fingerprint="cross-model-by-third-segment",
        )


def test_cross_model_time_bindings_are_consistent_and_frozen() -> None:
    payload = _governed_requirement().model_dump()
    payload["scope"] = {
        **payload["scope"],
        "dimension_refs": (
            "DIMENSION:20:1",
            "DIMENSION:21:1",
            "DIMENSION:22:1",
            "DIMENSION:21:2",
        ),
        "driver_metric_refs": ("METRIC:11:2",),
        "driver_relationships": (
            {
                "target_metric_ref": "METRIC:10:1",
                "driver_metric_ref": "METRIC:11:2",
                "relationship_type": "certified_driver",
                "dimension_refs": (),
                "dimension_refs_by_model": {
                    "1": ("DIMENSION:20:1",),
                    "2": ("DIMENSION:21:2",),
                },
                "relation_path": (1,),
                "time_roles": ("current", "previous"),
                "relationship_fingerprint": "cross-model-1",
            },
        ),
    }
    requirement = ResearchAgentRequirement(
        **{
            **payload,
            "time_bindings_by_model": {
                "1": [
                    {
                        "role": "current",
                        "expression": "本月",
                        "dimension_ref": "DIMENSION:20:1",
                        "normalized": {"kind": "absolute_range"},
                    },
                    {
                        "role": "previous",
                        "expression": "上月",
                        "dimension_ref": "DIMENSION:20:1",
                        "normalized": {"kind": "absolute_range"},
                    },
                ],
                "2": [
                    {
                        "role": "current",
                        "expression": "本月",
                        "dimension_ref": "DIMENSION:21:2",
                        "normalized": {"kind": "absolute_range"},
                    },
                    {
                        "role": "previous",
                        "expression": "上月",
                        "dimension_ref": "DIMENSION:21:2",
                        "normalized": {"kind": "absolute_range"},
                    },
                ],
            },
        }
    )
    assert set(requirement.time_bindings_by_model) == {"1", "2"}

    state = ResearchWorkingState(
        run_id="run-1",
        goal="分析变化",
        target_metric_refs=("METRIC:10:1",),
        time_bindings=requirement.time_bindings,
        time_bindings_by_model=requirement.time_bindings_by_model,
        scope_fingerprint="scope-1",
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
        budget_remaining={
            "iterations": 2,
            "queries": 4,
            "model_calls": 4,
            "duration_seconds": 100,
        },
    )
    with pytest.raises(ValueError, match="FROZEN_WHAT_CHANGED"):
        state.evolve(time_bindings_by_model={})


def test_cross_model_time_bindings_reject_invalid_model_role_and_dimension() -> None:
    base = _governed_requirement().model_dump()
    with pytest.raises(ValueError, match="MODEL_INVALID"):
        ResearchAgentRequirement(
            **{
                **base,
                "time_bindings_by_model": {"model-a": base["time_bindings"]},
            }
        )
    with pytest.raises(ValueError, match="MODEL_ROLE_MISMATCH"):
        ResearchAgentRequirement(
            **{
                **base,
                "time_bindings_by_model": {
                    "1": [
                        {
                            "role": "current",
                            "expression": "本月",
                            "dimension_ref": "DIMENSION:21:1",
                            "normalized": {"kind": "absolute_range"},
                        }
                    ]
                },
            }
        )
    with pytest.raises(ValueError, match="MODEL_DIMENSION_OUT_OF_SCOPE"):
        ResearchAgentRequirement(
            **{
                **base,
                "time_bindings_by_model": {
                    "1": [
                        {
                            "role": "current",
                            "expression": "本月",
                            "dimension_ref": "DIMENSION:999:1",
                            "normalized": {"kind": "absolute_range"},
                        },
                        {
                            "role": "previous",
                            "expression": "上月",
                            "dimension_ref": "DIMENSION:999:1",
                            "normalized": {"kind": "absolute_range"},
                        },
                    ]
                },
            }
        )
    with pytest.raises(ValueError, match="MODEL_OUT_OF_SCOPE"):
        ResearchAgentRequirement(
            **{
                **base,
                "time_bindings_by_model": {"2": base["time_bindings"]},
            }
        )


def test_cross_model_mapping_rejects_dimension_from_another_model() -> None:
    with pytest.raises(ValueError, match="DRIVER_MODEL_DIMENSIONS_INVALID"):
        ResearchDriverRelationship(
            target_metric_ref="METRIC:10:1",
            driver_metric_ref="METRIC:11:2",
            relationship_type="certified_driver",
            dimension_refs_by_model={
                "1": ("DIMENSION:20:1",),
                "2": ("DIMENSION:21:1",),
            },
            relation_path=(1,),
            time_roles=("current", "previous"),
            relationship_fingerprint="cross-model-invalid-dimension",
        )


def test_queries_cover_compare_breakdown_drilldown_result_filter_and_contribution() -> None:
    requirement = _governed_requirement()
    evidence = (_evidence(),)

    compare = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "time_ranges": ("current", "previous"),
            "comparison": "difference",
            "analysis": "compare",
        }
    )
    requirement.validate_query(compare)

    breakdown = ResearchSemanticQuery(
        **{
            **compare.model_dump(),
            "analysis": "breakdown",
            "dimensions": ("DIMENSION:21:1",),
        }
    )
    requirement.validate_query(breakdown)

    drilldown = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "analysis": "drilldown",
            "dimensions": ("DIMENSION:21:1",),
            "drilldown": {
                "hierarchy_id": "geo",
                "source_evidence_id": "evidence-1",
                "current_dimension_ref": "DIMENSION:20:1",
                "next_dimension_ref": "DIMENSION:21:1",
            },
        }
    )
    requirement.validate_query(drilldown, evidence)

    result_filter = ResearchSemanticQuery(
        **{
            **_query().model_dump(),
            "analysis": "filter_from_result",
            "evidence_value_filters": [
                {
                    "run_id": "run-1",
                    "evidence_id": "evidence-1",
                    "target_ref": "DIMENSION:20:1",
                    "column_ref": "DIMENSION:20:1",
                    "row_selector": {"rank": 1},
                }
            ],
        }
    )
    requirement.validate_query(result_filter, evidence)

    contribution = ResearchSemanticQuery(
        **{
            **compare.model_dump(),
            "analysis": "contribution",
            "comparison": "contribution",
            "dimensions": ("DIMENSION:21:1",),
        }
    )
    requirement.validate_query(contribution)


def test_query_rejects_non_adjacent_drilldown_and_invalid_contribution_scope() -> None:
    requirement = _governed_requirement()
    evidence = (_evidence(),)
    with pytest.raises(ValueError, match="DRILLDOWN_NOT_ADJACENT"):
        requirement.validate_query(
            ResearchSemanticQuery(
                **{
                    **_query().model_dump(),
                    "analysis": "drilldown",
                    "dimensions": ("DIMENSION:22:1",),
                    "drilldown": {
                        "hierarchy_id": "geo",
                        "source_evidence_id": "evidence-1",
                        "current_dimension_ref": "DIMENSION:20:1",
                        "next_dimension_ref": "DIMENSION:22:1",
                    },
                }
            ),
            evidence,
        )

    with pytest.raises(ValueError, match="CONTRIBUTION_DIMENSION_OUT_OF_SCOPE"):
        ResearchSemanticQuery(
            **{
                **_query().model_dump(),
                "analysis": "contribution",
                "comparison": "contribution",
                "dimensions": ("DIMENSION:20:1",),
                "time_ranges": ("current", "previous"),
            }
        ).validate_scope(requirement.scope)


def test_single_time_query_is_allowed_but_comparison_requires_two_roles() -> None:
    requirement = _requirement()
    requirement.validate_query(_query())
    with pytest.raises(ValueError, match="COMPARISON_TIME_ROLES_REQUIRED"):
        requirement.validate_query(
            ResearchSemanticQuery(
                **{
                    **_query().model_dump(),
                    "comparison": "difference",
                    "analysis": "compare",
                }
            )
        )


def test_execution_mode_is_strict_and_defaults_to_legacy() -> None:
    assert ResearchExecutionMode.LEGACY.value == "legacy"
    with pytest.raises(ValueError):
        ResearchExecutionMode("unsupported")
