from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chatbi.errors import ResultArtifactWriteError
from apps.chatbi.models import ChatBIResultArtifactRef
from apps.chatbi.models.dto.analysis_plan import (
    ResultSetKind,
    ResultSetRef,
    ResultSetSnapshot,
)
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchDriverRelationship,
    ResearchEvidence,
    ResearchEvidenceStatistics,
    ResearchEvidenceValueRef,
    ResearchLogicalColumn,
    ResearchQueryComparison,
    ResearchRowSelector,
    ResearchSemanticQuery,
    ResearchTimeBinding,
    ResearchTimeRole,
    ResearchVersionSnapshot,
    ToolErrorCode,
    ToolFailureStage,
)
from apps.chatbi.services.execution import PlanPipelineError
from apps.chatbi.services.research.semantic_query_builder import SemanticQueryBuilder
from apps.chatbi.services.research.semantic_runtime import SemanticQueryRuntime
from apps.retrieval import ExecutableAssetReference
from apps.tool.tools.semantic_contracts import SemanticAssetScope
from tests.chatbi.test_research_agent_contracts import (
    _governed_requirement,
    _query,
    _version,
)
from tests.semantic.test_semantic_query_planning import _schema


def _scope(
    *, dataset_id: int = 1, schema=None, permission_version: str | None = "permission-v1"
) -> SemanticAssetScope:
    return SemanticAssetScope(
        workspace_id=1,
        user_id=1,
        datasource_id=1,
        dataset_id=dataset_id,
        retrieval_id="retrieval-1",
        allowed_assets=(
            ExecutableAssetReference(asset_type="METRIC", asset_id=10, model_id=1),
            ExecutableAssetReference(asset_type="DIMENSION", asset_id=20, model_id=1),
        ),
        schema_snapshot=schema,
        permission_version=permission_version,
        scope_fingerprint="scope-1",
        permission_fingerprint="permission-1",
    )


def _cross_model_requirement() -> ResearchAgentRequirement:
    base = _governed_requirement()
    scope = base.scope.model_copy(
        update={
            "target_metric_refs": ("METRIC:10:1",),
            "driver_metric_refs": ("METRIC:11:2",),
            "dimension_refs": ("DIMENSION:20:1", "DIMENSION:21:2"),
            "hierarchies": (),
            "allowed_filter_refs": ("DIMENSION:20:1", "DIMENSION:21:2"),
            "contribution_metric_refs": ("METRIC:10:1", "METRIC:11:2"),
            "contribution_dimension_refs": ("DIMENSION:20:1",),
            "driver_relationships": (
                ResearchDriverRelationship(
                    target_metric_ref="METRIC:10:1",
                    driver_metric_ref="METRIC:11:2",
                    relationship_type="certified_driver",
                    dimension_refs=(),
                    dimension_refs_by_model={
                        "1": ("DIMENSION:20:1",),
                        "2": ("DIMENSION:21:2",),
                    },
                    relation_path=(1,),
                    time_roles=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
                    relationship_fingerprint="cross-model-runtime-1",
                ),
            ),
        }
    )
    return base.model_copy(
        update={
            "target_metric_refs": ("METRIC:10:1",),
            "time_bindings_by_model": {
                "1": base.time_bindings,
                "2": (
                    ResearchTimeBinding(
                        role=ResearchTimeRole.CURRENT,
                        expression="本月",
                        dimension_ref="DIMENSION:21:2",
                        normalized={"kind": "absolute_range", "start": "2026-08-01"},
                    ),
                    ResearchTimeBinding(
                        role=ResearchTimeRole.PREVIOUS,
                        expression="上月",
                        dimension_ref="DIMENSION:21:2",
                        normalized={"kind": "absolute_range", "start": "2026-07-01"},
                    ),
                ),
            },
            "scope": scope,
        }
    )


def _cross_model_scope() -> SemanticAssetScope:
    return SemanticAssetScope(
        workspace_id=1,
        user_id=1,
        datasource_id=1,
        dataset_id=1,
        retrieval_id="retrieval-cross-model",
        allowed_assets=(
            ExecutableAssetReference(asset_type="METRIC", asset_id=10, model_id=1),
            ExecutableAssetReference(asset_type="DIMENSION", asset_id=20, model_id=1),
            ExecutableAssetReference(asset_type="METRIC", asset_id=11, model_id=2),
            ExecutableAssetReference(asset_type="DIMENSION", asset_id=21, model_id=2),
        ),
    )


def test_builder_creates_plan_independent_analysis_spec() -> None:
    requirement = _governed_requirement()
    spec = SemanticQueryBuilder(semantic_scope=_scope()).build(
        _query(), requirement=requirement
    )

    assert [item.id for item in spec.query_requirements] == ["current"]
    assert spec.post_calculations == ()
    assert spec.result_contract is not None
    assert spec.result_contract.primary_requirement_id == "current"
    assert spec.runtime["run_id"] == "run-1"


def test_builder_creates_current_previous_difference_dag() -> None:
    requirement = _governed_requirement()
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
        metrics=("METRIC:10:1",),
        dimensions=("DIMENSION:20:1",),
        time_ranges=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
        comparison=ResearchQueryComparison.DIFFERENCE,
        analysis="compare",
        purpose="对比当前期和前期",
    )
    spec = SemanticQueryBuilder(semantic_scope=_scope()).build(
        query, requirement=requirement
    )

    assert [item.id for item in spec.query_requirements] == ["current", "previous"]
    assert [item.id for item in spec.post_calculations] == ["comparison"]
    assert spec.post_calculations[0].inputs == ("current", "previous")
    assert spec.result_contract is not None
    assert spec.result_contract.primary_requirement_id == "comparison"


def test_builder_maps_cross_model_dimensions_to_one_logical_join_key() -> None:
    requirement = _cross_model_requirement()
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=requirement.version_snapshot,
        metrics=("METRIC:10:1", "METRIC:11:2"),
        dimensions=("DIMENSION:20:1",),
        time_ranges=(ResearchTimeRole.CURRENT,),
        analysis="breakdown",
        purpose="跨模型验证当前期",
    )
    spec = SemanticQueryBuilder(semantic_scope=_cross_model_scope()).build(
        query,
        requirement=requirement,
    )

    assert [item.id for item in spec.query_requirements] == [
        "current:m1",
        "current:m2",
    ]
    assert spec.query_requirements[0].output_aliases == {20: "dimension_1"}
    assert spec.query_requirements[1].output_aliases == {21: "dimension_1"}
    assert spec.post_calculations[0].join_keys == ("dimension_1",)
    assert spec.post_calculations[0].inputs == ("current:m1", "current:m2")


def test_builder_rejects_cross_model_query_without_each_model_time_binding() -> None:
    requirement = _cross_model_requirement().model_copy(
        update={"time_bindings_by_model": {}}
    )
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=requirement.version_snapshot,
        metrics=("METRIC:10:1", "METRIC:11:2"),
        dimensions=("DIMENSION:20:1",),
        time_ranges=(ResearchTimeRole.CURRENT,),
        analysis="breakdown",
        purpose="跨模型缺少时间绑定",
    )

    with pytest.raises(ValueError, match="UNSUPPORTED_CAPABILITY"):
        SemanticQueryBuilder(semantic_scope=_cross_model_scope()).build(
            query,
            requirement=requirement,
        )


def test_builder_rejects_multi_metric_contribution_instead_of_ignoring_metrics() -> None:
    requirement = _cross_model_requirement()
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=requirement.version_snapshot,
        metrics=("METRIC:10:1", "METRIC:11:2"),
        dimensions=("DIMENSION:20:1",),
        time_ranges=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
        comparison=ResearchQueryComparison.CONTRIBUTION,
        analysis="contribution",
        purpose="跨模型贡献度",
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_CAPABILITY"):
        SemanticQueryBuilder(semantic_scope=_cross_model_scope()).build(
            query,
            requirement=requirement,
        )


def test_builder_creates_single_metric_contribution_and_reconciliation_dag() -> None:
    requirement = _governed_requirement()
    contribution_scope = _scope().model_copy(
        update={
            "allowed_assets": (
                ExecutableAssetReference(
                    asset_type="METRIC", asset_id=10, model_id=1
                ),
                ExecutableAssetReference(
                    asset_type="DIMENSION", asset_id=21, model_id=1
                ),
            )
        }
    )
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=requirement.version_snapshot,
        metrics=("METRIC:10:1",),
        dimensions=("DIMENSION:21:1",),
        time_ranges=(ResearchTimeRole.CURRENT, ResearchTimeRole.PREVIOUS),
        comparison=ResearchQueryComparison.CONTRIBUTION,
        analysis="contribution",
        purpose="计算各区域对变化的贡献",
    )

    spec = SemanticQueryBuilder(semantic_scope=contribution_scope).build(
        query,
        requirement=requirement,
    )

    assert [item.id for item in spec.query_requirements] == [
        "current",
        "previous",
        "current:total",
        "previous:total",
    ]
    assert [item.id for item in spec.post_calculations] == [
        "breakdown_difference",
        "total_difference",
        "contribution",
    ]
    assert all(
        item.query_shape["needs_group_by"] is False
        for item in spec.query_requirements
        if item.id.endswith(":total")
    )
    assert spec.post_calculations[-1].inputs == (
        "breakdown_difference",
        "total_difference",
    )
    assert spec.result_contract is not None
    assert spec.result_contract.primary_requirement_id == "contribution"


def test_builder_uses_verified_evidence_value_for_adjacent_drilldown() -> None:
    requirement = _governed_requirement()
    evidence = ResearchEvidence(
        run_id="run-1",
        evidence_id="evidence-1",
        source_tool_call={"run_id": "run-1", "tool_call_id": "tool-1"},
        result_ref={"run_id": "run-1", "result_id": "result-1"},
        iteration=0,
        purpose="定位上一层对象",
        metric_refs=("METRIC:10:1",),
        dimension_refs=("DIMENSION:20:1",),
        time_ranges=(ResearchTimeRole.CURRENT,),
        logical_columns=(
            ResearchLogicalColumn(
                asset_ref="DIMENSION:20:1",
                value_role="group_key",
                result_field="dimension_1",
            ),
        ),
        statistics=ResearchEvidenceStatistics(row_count=1),
        version_snapshot=requirement.version_snapshot,
    )
    drilldown_scope = _scope().model_copy(
        update={
            "allowed_assets": (
                ExecutableAssetReference(
                    asset_type="METRIC", asset_id=10, model_id=1
                ),
                ExecutableAssetReference(
                    asset_type="DIMENSION", asset_id=20, model_id=1
                ),
                ExecutableAssetReference(
                    asset_type="DIMENSION", asset_id=21, model_id=1
                ),
            )
        }
    )
    query = ResearchSemanticQuery(
        run_id="run-1",
        scope_fingerprint="scope-1",
        version_snapshot=requirement.version_snapshot,
        metrics=("METRIC:10:1",),
        dimensions=("DIMENSION:21:1",),
        time_ranges=(ResearchTimeRole.CURRENT,),
        evidence_value_filters=(
            ResearchEvidenceValueRef(
                run_id="run-1",
                evidence_id="evidence-1",
                target_ref="DIMENSION:20:1",
                column_ref="DIMENSION:20:1",
                row_selector=ResearchRowSelector(rank=1),
            ),
        ),
        analysis="drilldown",
        drilldown={
            "hierarchy_id": "geo",
            "source_evidence_id": "evidence-1",
            "current_dimension_ref": "DIMENSION:20:1",
            "next_dimension_ref": "DIMENSION:21:1",
        },
        purpose="沿已治理层级继续下钻",
    )

    spec = SemanticQueryBuilder(semantic_scope=drilldown_scope).build(
        query,
        requirement=requirement,
        evidence=(evidence,),
        evidence_value_resolver=lambda _value_ref, _evidence: "华东",
    )

    assert spec.query_requirements[0].group_by[0]["asset_id"] == 21
    assert spec.query_requirements[0].filters == (
        {
            "asset_id": 20,
            "model_id": 1,
            "operator": "=",
            "value": "华东",
        },
    )


def test_runtime_rejects_tenant_mismatch_before_execution() -> None:
    class Context:
        oid = 999
        user_id = 1
        datasource_id = 1
        dataset_id = 1
        state = {}
        semantic_asset_scope = _scope()

    class NeverExecute:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("不应在租户校验失败后执行")

    result = SemanticQueryRuntime(NeverExecute()).execute(
        Context(), _query(), requirement=_governed_requirement()
    )

    assert result.status == "failed"
    assert result.error_code == ToolErrorCode.PERMISSION_DENIED


def test_runtime_allows_unactivated_permission_version_on_both_sides() -> None:
    """permission_version 是 P2-4 资产级授权的预留钩子：当前索引投影与路由期
    检索请求都不填充，生产栈端到端为 None（run 1275 冒烟回归）。两侧同时
    缺失必须放行，权限由 permission_fingerprint 门继续完整约束。"""

    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:243"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})
    runtime_scope = _scope(dataset_id=243, schema=schema, permission_version=None)

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = runtime_scope
        # 生产 AgentToolContext 的 permission_version 默认为 None，甚至可以缺属性。
        permission_version = None

    class FakeExecutionService:
        def execute(self, _context, _spec, *, plan_id):
            return SimpleNamespace(
                execution_records={
                    "q:current": {
                        "result_set_id": f"result:{plan_id}:q:current",
                        "fields": ["dimension_1", "10"],
                    }
                },
                primary_execution={
                    "result_set_id": f"result:{plan_id}:q:current",
                    "fields": ["dimension_1", "10"],
                },
                primary_rows=[{"dimension_1": "华东", "10": 10}],
            )

    result = SemanticQueryRuntime(FakeExecutionService()).execute(
        Context(), query, requirement=requirement
    )

    assert result.status == "succeeded"


@pytest.mark.parametrize(
    ("context_version", "scope_version"),
    [("permission-v2", None), (None, "permission-v2"), ("a", "b")],
)
def test_runtime_denies_permission_version_mismatch(
    context_version: str | None, scope_version: str | None
) -> None:
    """任一侧提供版本而另一侧缺失或不一致，都是边界破坏，必须拒绝。"""

    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:243"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = _scope(
            dataset_id=243, schema=schema, permission_version=scope_version
        )
        permission_version = context_version

    class NeverExecute:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("不应在版本不一致时执行")

    result = SemanticQueryRuntime(NeverExecute()).execute(
        Context(), query, requirement=requirement
    )

    assert result.status == "failed"
    assert result.error_code == ToolErrorCode.PERMISSION_DENIED


def test_runtime_projects_primary_result_to_evidence() -> None:
    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:243"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})
    runtime_scope = _scope(dataset_id=243, schema=schema)

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = runtime_scope
        permission_version = "permission-v1"

    class FakeExecutionService:
        def execute(self, _context, _spec, *, plan_id):
            return SimpleNamespace(
                execution_records={
                    "q:current": {
                        "result_set_id": f"result:{plan_id}:q:current",
                        "fields": ["dimension_1", "10"],
                    }
                },
                primary_execution={
                    "result_set_id": f"result:{plan_id}:q:current",
                    "fields": ["dimension_1", "10"],
                },
                primary_rows=[{"dimension_1": "华东", "10": 10}],
            )

    result = SemanticQueryRuntime(FakeExecutionService()).execute(
        Context(), query, requirement=requirement
    )

    assert result.status == "succeeded"
    assert result.result_refs[0].result_id.endswith(":q:current")
    assert result.evidence[0].result_ref.result_id == result.primary_result_id


def test_runtime_does_not_match_dataset_24_to_dataset_243() -> None:
    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:24"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = _scope(dataset_id=243, schema=schema)
        permission_version = "permission-v1"

    result = SemanticQueryRuntime(object()).execute(
        Context(),
        query,
        requirement=requirement,
    )

    assert result.error_code == ToolErrorCode.PERMISSION_DENIED
    assert result.message == ToolErrorCode.PERMISSION_DENIED.value


def test_runtime_propagates_unknown_execution_exception() -> None:
    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:243"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = _scope(dataset_id=243, schema=schema)
        permission_version = "permission-v1"

    class UnknownExecutionFailure:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("unexpected execution bug")

    with pytest.raises(RuntimeError, match="unexpected execution bug"):
        SemanticQueryRuntime(UnknownExecutionFailure()).execute(
            Context(),
            query,
            requirement=requirement,
        )


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_stage"),
    [
        (
            PlanPipelineError("PLAN_FANOUT_DETECTED"),
            ToolErrorCode.FANOUT_DETECTED,
            ToolFailureStage.PROJECTION,
        ),
        (
            PlanPipelineError("PLAN_RECONCILIATION_FAILED"),
            ToolErrorCode.RECONCILIATION_FAILED,
            ToolFailureStage.PROJECTION,
        ),
        (
            ResultArtifactWriteError("artifact write failed"),
            ToolErrorCode.RESULT_STORE_FAILED,
            ToolFailureStage.PERSISTENCE,
        ),
    ],
)
def test_runtime_classifies_declared_execution_failures(
    failure: Exception,
    expected_code: ToolErrorCode,
    expected_stage: ToolFailureStage,
) -> None:
    schema = _schema()
    schema.data_set.id = 243
    requirement = _governed_requirement().model_copy(
        update={
            "scope": _governed_requirement().scope.model_copy(
                update={"dataset_ref": "ASSET:dataset:243"}
            ),
            "version_snapshot": ResearchVersionSnapshot(
                schema_version=schema.schema_version,
                contract_version=schema.contract_version,
                schema_fingerprint=schema.schema_fingerprint,
                scope_fingerprint="scope-1",
                permission_fingerprint="permission-1",
            ),
        }
    )
    query = _query().model_copy(update={"version_snapshot": requirement.version_snapshot})

    class Context:
        oid = 1
        user_id = 1
        datasource_id = 1
        dataset_id = 243
        state = {"research_run_id": "run-1"}
        semantic_asset_scope = _scope(dataset_id=243, schema=schema)
        permission_version = "permission-v1"

    class DeclaredFailure:
        def execute(self, *_args, **_kwargs):
            raise failure

    result = SemanticQueryRuntime(DeclaredFailure()).execute(
        Context(),
        query,
        requirement=requirement,
    )

    assert result.status == "failed"
    assert result.error_code is expected_code
    assert result.failure_stage is expected_stage


def test_evidence_value_resolver_reads_full_result_not_sample_rows() -> None:
    result_ref = ResultSetRef(
        result_set_id="result:plan-1:q-current",
        plan_id="plan-1",
        node_id="q-current",
        kind=ResultSetKind.QUERY,
        artifact_ref=ChatBIResultArtifactRef(
            artifact_id="artifact-1",
            kind="analysis_result_set",
            content_type="application/json",
            size=10,
            digest="digest-1",
        ),
        fields=("dimension_1", "metric"),
        row_count=2,
    )

    class ResultStore:
        def read(self, ref, **_kwargs):
            assert ref.result_set_id == result_ref.result_set_id
            return ResultSetSnapshot(
                ref=ref,
                rows=(
                    {"dimension_1": "first", "metric": 1},
                    {"dimension_1": "second", "metric": 2},
                ),
            )

    class Context:
        execution_id = "agent:1"
        chat_id = 1
        record_id = 1
        result_store = ResultStore()
        state = {
            "result_sets": {
                result_ref.result_set_id: result_ref.model_dump(mode="json")
            }
        }

    evidence = ResearchEvidence(
        run_id="run-1",
        evidence_id="evidence-1",
        source_tool_call={"run_id": "run-1", "tool_call_id": "tool-1"},
        result_ref={"run_id": "run-1", "result_id": result_ref.result_set_id},
        iteration=0,
        purpose="读取完整结果",
        metric_refs=("METRIC:10:1",),
        dimension_refs=("DIMENSION:20:1",),
        logical_columns=(
            ResearchLogicalColumn(
                asset_ref="DIMENSION:20:1",
                value_role="group_key",
                result_field="dimension_1",
            ),
        ),
        statistics=ResearchEvidenceStatistics(row_count=2),
        # 样本故意放入错误值，确保不会被当作筛选值来源。
        sample_rows=({"dimension_1": "sample-only"},),
        version_snapshot=ResearchVersionSnapshot.model_validate(_version()),
    )
    value_ref = ResearchEvidenceValueRef(
        run_id="run-1",
        evidence_id="evidence-1",
        target_ref="DIMENSION:20:1",
        column_ref="DIMENSION:20:1",
        row_selector=ResearchRowSelector(rank=2),
    )

    value = SemanticQueryRuntime(object())._resolve_evidence_value(
        Context(),
        value_ref,
        evidence,
    )

    assert value == "second"


def test_builder_ref_semantics_follow_production_asset_first_canon() -> None:
    """引用格式 ``KIND:资产ID:模型ID``：解析与资产键序必须与生产冻结一致。

    requirements.py 以 ``f"METRIC:{id}:{model}"`` 产出引用；builder 曾把第二段
    当模型 ID 读，导致真实 Scope 的资产永远无法解析（run 1264 冒烟暴露）。
    """

    assert SemanticQueryBuilder._parse_ref("METRIC:271:246") == (246, 271)
    assert SemanticQueryBuilder._ref_key("METRIC", 271, 246) == "METRIC:271:246"

    sentinel = object()
    resolved = SemanticQueryBuilder._resolve_asset(
        "METRIC:271:246",
        "METRIC",
        {SemanticQueryBuilder._ref_key("METRIC", 271, 246): sentinel},
        None,
    )
    assert resolved is sentinel
