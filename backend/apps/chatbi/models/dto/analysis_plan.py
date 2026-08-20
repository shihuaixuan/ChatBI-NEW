"""AnalysisPlan 与命名结果集的稳定数据契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.result_artifact import ChatBIResultArtifactRef


class AnalysisTaskType(StrEnum):
    QUERY = "query"
    COMPUTE = "compute"


class ComputeOperation(StrEnum):
    MERGE = "merge"
    DIFFERENCE = "difference"
    GROWTH_RATE = "growth_rate"
    SHARE = "share"
    RATIO = "ratio"
    TOPN_OTHER = "topn_other"
    PIVOT = "pivot"
    EXPR = "expr"
    CONTRIBUTION = "contribution"


class AnalysisPlanStatus(StrEnum):
    DRAFT = "DRAFT"
    PROVEN = "PROVEN"
    REJECTED = "REJECTED"


class ResultSetKind(StrEnum):
    QUERY = "query"
    COMPUTE = "compute"


class QueryTaskSpec(BaseModel):
    """规划器输出的语义查询草案；编译与证明结果不混入该对象。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    metric_ids: tuple[int, ...] = ()
    dimension_ids: tuple[int, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    time_range: dict[str, Any] | None = None
    time_dimension_id: int | None = Field(default=None, gt=0)
    time_grain: str | None = None
    select_mode: str | None = None
    query_shape: str | None = None
    order_by: tuple[dict[str, Any], ...] = ()
    limit: int | None = Field(default=None, gt=0)
    having: tuple[dict[str, Any], ...] = ()
    time_offset: dict[str, Any] | None = None


class CompiledQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_fingerprint: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    tables: tuple[str, ...] = ()


class QueryTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: Literal[AnalysisTaskType.QUERY] = AnalysisTaskType.QUERY
    # 查询任务只能引用执行需求，不能由规划模型重新拼装查询口径。
    source_requirement_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    spec: QueryTaskSpec
    compiled: CompiledQuery | None = None


class ComputeDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    expr: str = Field(min_length=1)


class ComputeTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: Literal[AnalysisTaskType.COMPUTE] = AnalysisTaskType.COMPUTE
    # 计算任务只能引用执行需求中的后置计算定义。
    source_calculation_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    operation: ComputeOperation
    inputs: tuple[str, ...] = Field(min_length=1)
    join_on: tuple[str, ...] = ()
    derive: tuple[ComputeDerivation, ...] = ()
    options: dict[str, Any] = Field(default_factory=dict)


AnalysisTask = Annotated[QueryTask | ComputeTask, Field(discriminator="type")]


class PlanEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)


class PresentationHint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_result: str = Field(min_length=1, max_length=128)
    supporting_results: tuple[str, ...] = ()
    ordered_results: tuple[str, ...] = ()
    completion_policy: Literal["require_primary"] = "require_primary"
    chart_hint: str | None = None

    @model_validator(mode="after")
    def validate_result_declarations(self) -> PresentationHint:
        declared = {self.primary_result, *self.supporting_results}
        if len(declared) != 1 + len(self.supporting_results):
            raise ValueError("ANALYSIS_PLAN_PRESENTATION_RESULT_DUPLICATED")
        if self.ordered_results and (
            len(self.ordered_results) != len(set(self.ordered_results))
            or set(self.ordered_results) != declared
        ):
            raise ValueError("ANALYSIS_PLAN_PRESENTATION_ORDER_INVALID")
        return self


class PlanValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AnalysisPlanStatus = AnalysisPlanStatus.DRAFT
    reports: tuple[dict[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()


class AnalysisPlan(BaseModel):
    """可持久化、可生成 JSON Schema 的分析执行计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    version: int = Field(default=1, ge=1)
    tasks: tuple[AnalysisTask, ...] = Field(min_length=1)
    edges: tuple[PlanEdge, ...] = ()
    presentation: PresentationHint
    validation: PlanValidation = Field(default_factory=PlanValidation)

    @model_validator(mode="after")
    def validate_references(self) -> AnalysisPlan:
        """在模型边界统一约束计划内部引用，完整 DAG 校验由 P1-3 负责。"""

        task_ids = [task.id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("ANALYSIS_PLAN_TASK_ID_DUPLICATED")
        known_ids = set(task_ids)
        for edge in self.edges:
            if edge.source == edge.target:
                raise ValueError("ANALYSIS_PLAN_SELF_EDGE")
            if edge.source not in known_ids or edge.target not in known_ids:
                raise ValueError("ANALYSIS_PLAN_EDGE_NODE_UNKNOWN")
        presentation_results = {
            self.presentation.primary_result,
            *self.presentation.supporting_results,
        }
        if not presentation_results <= known_ids:
            raise ValueError("ANALYSIS_PLAN_PRIMARY_RESULT_UNKNOWN")
        for task in self.tasks:
            if isinstance(task, ComputeTask) and any(
                item not in known_ids for item in task.inputs
            ):
                raise ValueError("ANALYSIS_PLAN_COMPUTE_INPUT_UNKNOWN")
        return self


class ResultSetRef(BaseModel):
    """Run 状态中保存的轻量结果集引用，不包含全量行。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_set_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=128)
    kind: ResultSetKind
    artifact_ref: ChatBIResultArtifactRef
    fields: tuple[str, ...] = ()
    row_count: int = Field(ge=0)
    attempt: int = Field(default=1, ge=1)
    source_sql: str | None = None
    semantic_refs: tuple[dict[str, Any], ...] = ()
    created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_result_set_id(self) -> ResultSetRef:
        if self.result_set_id != build_result_set_id(self.plan_id, self.node_id):
            raise ValueError("RESULT_SET_ID_MISMATCH")
        return self


class ResultSetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ResultSetRef
    rows: tuple[dict[str, Any], ...] = ()
    numeric_stats: dict[str, dict[str, int | float | str]] = Field(default_factory=dict)


class ResultSetSummary(BaseModel):
    """供规划与回答阶段消费的有界结果摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_set_id: str
    fields: tuple[str, ...] = ()
    row_count: int = Field(ge=0)
    sample_rows: tuple[dict[str, Any], ...] = ()
    numeric_stats: dict[str, dict[str, int | float | str]] = Field(default_factory=dict)


def build_result_set_id(plan_id: str, node_id: str) -> str:
    """结果集命名是跨执行器共享的不变量，只允许由此入口生成。"""

    normalized_plan_id = plan_id.strip()
    normalized_node_id = node_id.strip()
    if not normalized_plan_id or not normalized_node_id:
        raise ValueError("RESULT_SET_ID_PART_REQUIRED")
    return f"result:{normalized_plan_id}:{normalized_node_id}"


__all__ = [
    "AnalysisPlan",
    "AnalysisPlanStatus",
    "AnalysisTask",
    "AnalysisTaskType",
    "CompiledQuery",
    "ComputeDerivation",
    "ComputeOperation",
    "ComputeTask",
    "PlanEdge",
    "PlanValidation",
    "PresentationHint",
    "QueryTask",
    "QueryTaskSpec",
    "ResultSetKind",
    "ResultSetRef",
    "ResultSetSnapshot",
    "ResultSetSummary",
    "build_result_set_id",
]
