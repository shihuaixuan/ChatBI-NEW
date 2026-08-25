"""阶段 3 统一 Analysis Evidence 协议测试。"""

from __future__ import annotations

import pytest

from apps.chatbi.models.dto.analysis_evidence import (
    AnalysisEvidenceColumn,
    AnalysisEvidenceDependency,
)
from apps.chatbi.services.evidence import (
    EvidenceRegistry,
    build_analysis_evidence,
    build_analysis_version_snapshot,
)
from apps.chatbi.services.research.completion import evaluate_structural_coverage
from tests.chatbi.test_research_agent_contracts import _requirement


def _version():
    return build_analysis_version_snapshot(
        asset_snapshot={
            "schema_version": 22,
            "contract_version": 3,
            "schema_fingerprint": "schema-1",
        },
        semantic_scope={
            "scope_fingerprint": "scope-1",
            "permission_fingerprint": "permission-1",
        },
    )


def _evidence(
    *,
    run_id: str = "run-1",
    mode: str = "fast",
    plan_id: str = "plan-1",
    node_id: str = "query-1",
    iteration: int = 0,
    dependencies: tuple[AnalysisEvidenceDependency, ...] = (),
    purpose: str = "测试 Evidence",
):
    return build_analysis_evidence(
        run_id=run_id,
        mode=mode,  # type: ignore[arg-type]
        plan_id=plan_id,
        node_id=node_id,
        tool_call_id=f"tool-{node_id}",
        result_set_id=f"result:{plan_id}:{node_id}",
        fields=["metric"],
        rows=[{"metric": 100}],
        row_count=1,
        metric_refs=("METRIC:10:1",),
        purpose=purpose,
        iteration=iteration,
        dependencies=dependencies,
        version_snapshot=_version(),
    )


def test_registry_accepts_fast_plan_and_research_in_one_run() -> None:
    state: dict[str, object] = {}
    registry = EvidenceRegistry(state)

    for mode in ("fast", "plan", "research"):
        registry.register(
            _evidence(
                mode=mode,
                plan_id=f"{mode}-plan",
                node_id=f"{mode}-node",
            )
        )

    assert {item.mode for item in registry.evidences()} == {
        "fast",
        "plan",
        "research",
    }
    assert state["analysis_evidence"]


def test_registry_validates_dependencies_and_allows_same_id_completion_update() -> None:
    state: dict[str, object] = {}
    registry = EvidenceRegistry(state)
    parent = _evidence(node_id="parent")
    registry.register(parent)
    child = _evidence(
        mode="plan",
        plan_id="plan-2",
        node_id="child",
        iteration=1,
        dependencies=(
            AnalysisEvidenceDependency(
                evidence_id=parent.evidence_id,
                relation="difference",
                source_iteration=0,
            ),
        ),
    )
    registry.register(child)

    updated = child.model_copy(
        update={
            "purpose": "服务端补充列映射",
            "logical_columns": (
                AnalysisEvidenceColumn(
                    asset_ref="METRIC:10:1",
                    value_role="difference",
                    result_field="metric",
                ),
            ),
        }
    )
    registry.register(updated)

    assert registry.get(child.evidence_id) == updated
    assert len(registry.evidences()) == 2

    restored_state = {
        "analysis_evidence": {
            parent.evidence_id: parent.model_dump(mode="json"),
            child.evidence_id: child.model_dump(mode="json"),
        }
    }
    EvidenceRegistry(restored_state).merge((updated,))
    assert EvidenceRegistry(restored_state).get(child.evidence_id) == updated


def test_registry_rejects_cross_run_evidence() -> None:
    state: dict[str, object] = {}
    registry = EvidenceRegistry(state)
    registry.register(_evidence())

    with pytest.raises(ValueError, match="ANALYSIS_EVIDENCE_CROSS_RUN"):
        registry.register(_evidence(run_id="run-2", node_id="other"))


def test_version_snapshot_rejects_missing_governance_fingerprint() -> None:
    with pytest.raises(
        ValueError,
        match="ANALYSIS_EVIDENCE_PERMISSION_FINGERPRINT_REQUIRED",
    ):
        build_analysis_version_snapshot(
            asset_snapshot={
                "schema_version": 22,
                "contract_version": 3,
                "schema_fingerprint": "schema-1",
            },
            semantic_scope={"scope_fingerprint": "scope-1"},
        )


def test_merge_missing_preserves_existing_unified_evidence() -> None:
    state: dict[str, object] = {}
    registry = EvidenceRegistry(state)
    existing = _evidence(purpose="统一台账规范记录")
    registry.register(existing)

    registry.merge_missing((_evidence(purpose="旧 Research 投影"),))

    assert registry.get(existing.evidence_id) == existing


def test_completion_reads_unified_evidence() -> None:
    requirement = _requirement()
    evidence = _evidence()

    evaluation = evaluate_structural_coverage(
        requirement,
        [evidence],
        premise_result={"status": "supported"},
    )

    assert evaluation.minimum_requirements_met is True
    assert evaluation.core_supported is True


def test_completion_only_reports_minimum_structural_coverage() -> None:
    """字段覆盖满足时仍不能据此判断 Evidence 内容已经回答用户问题。"""

    requirement = _requirement()
    evidence = _evidence(purpose="只有结构字段，没有原因解释内容")

    evaluation = evaluate_structural_coverage(
        requirement,
        [evidence],
        premise_result={"status": "supported"},
    )

    assert evaluation.minimum_requirements_met is True
    assert evaluation.missing_requirements == ()
