"""统一 Plan-and-Solve Runtime 使用的计划执行状态协议。"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.chatbi.models.dto.analysis_plan import AnalysisPlan, ComputeTask

PLAN_EXECUTION_STATE_KEY = "plan_execution_state"


class PlanNodeExecutionStatus(StrEnum):
    """统一计划持久化的节点状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_DEPENDENCY = "skipped_dependency"
    CANCELLED = "cancelled"


class UnifiedPlanNode(BaseModel):
    """统一计划保存可恢复执行所需的完整节点事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    task_type: Literal["query", "compute", "inspect"]
    dependencies: tuple[str, ...] = ()
    tool_name: str = Field(min_length=1, max_length=128)
    source: Literal["initial", "append"]
    gap_id: str | None = Field(default=None, min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dependencies(self) -> UnifiedPlanNode:
        if self.id in self.dependencies:
            raise ValueError("PLAN_NODE_SELF_DEPENDENCY")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("PLAN_NODE_DEPENDENCY_DUPLICATED")
        if self.tool_name in {
            "query_semantic_data",
            "compute_evidence",
            "inspect_evidence",
        } and not self.arguments:
            raise ValueError("PLAN_NODE_ARGUMENTS_REQUIRED")
        return self


class UnifiedPlanTaskState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PlanNodeExecutionStatus = PlanNodeExecutionStatus.PENDING
    attempt: int = Field(default=0, ge=0)
    tool_call_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    error_code: str | None = None


class UnifiedPlanExecutionState(BaseModel):
    """可持久化、可恢复并支持受控追加的统一计划状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1)
    nodes: tuple[UnifiedPlanNode, ...] = ()
    execution_batches: tuple[tuple[str, ...], ...] = ()
    task_states: dict[str, UnifiedPlanTaskState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_state(self) -> UnifiedPlanExecutionState:
        node_ids = tuple(node.id for node in self.nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("PLAN_NODE_ID_DUPLICATED")
        known = set(node_ids)
        if set(self.task_states) != known:
            raise ValueError("PLAN_TASK_STATE_NOT_MATCHED")
        for node in self.nodes:
            if not set(node.dependencies) <= known:
                raise ValueError("PLAN_NODE_DEPENDENCY_UNKNOWN")
        flattened = tuple(item for batch in self.execution_batches for item in batch)
        if len(flattened) != len(set(flattened)) or set(flattened) != known:
            raise ValueError("PLAN_EXECUTION_BATCH_NOT_MATCHED")
        return self


def _execution_batches(nodes: Sequence[UnifiedPlanNode]) -> tuple[tuple[str, ...], ...]:
    """由依赖关系统一生成稳定拓扑批次。"""

    by_id = {node.id: node for node in nodes}
    indegree = {node.id: len(node.dependencies) for node in nodes}
    followers: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            if dependency not in by_id:
                raise ValueError("PLAN_NODE_DEPENDENCY_UNKNOWN")
            followers[dependency].append(node.id)
    frontier = deque(node.id for node in nodes if indegree[node.id] == 0)
    batches: list[tuple[str, ...]] = []
    visited = 0
    while frontier:
        batch = tuple(frontier)
        frontier.clear()
        batches.append(batch)
        visited += len(batch)
        for node_id in batch:
            for follower in followers[node_id]:
                indegree[follower] -= 1
                if indegree[follower] == 0:
                    frontier.append(follower)
    if visited != len(nodes):
        raise ValueError("PLAN_DAG_CYCLE")
    return tuple(batches)


def build_analysis_plan_execution_state(
    plan: AnalysisPlan,
) -> UnifiedPlanExecutionState:
    """把 AnalysisPlan 投影到统一的可追加执行状态。"""

    dependencies: dict[str, list[str]] = {task.id: [] for task in plan.tasks}
    for edge in plan.edges:
        dependencies[edge.target].append(edge.source)
    nodes = tuple(
        UnifiedPlanNode(
            id=task.id,
            task_type="compute" if isinstance(task, ComputeTask) else "query",
            dependencies=tuple(dependencies[task.id]),
            tool_name="compute" if isinstance(task, ComputeTask) else "query",
            source="initial",
        )
        for task in plan.tasks
    )
    return UnifiedPlanExecutionState(
        plan_id=plan.id,
        revision=plan.version,
        nodes=nodes,
        execution_batches=_execution_batches(nodes),
        task_states={node.id: UnifiedPlanTaskState() for node in nodes},
    )


def build_plan_execution_state(
    plan_id: str,
    nodes: Sequence[UnifiedPlanNode] = (),
) -> UnifiedPlanExecutionState:
    """建立统一可追加 DAG；空计划允许 Planner 根据当前上下文生成首轮节点。"""

    frozen_nodes = tuple(nodes)
    return UnifiedPlanExecutionState(
        plan_id=plan_id,
        nodes=frozen_nodes,
        execution_batches=_execution_batches(frozen_nodes),
        task_states={node.id: UnifiedPlanTaskState() for node in frozen_nodes},
    )


def append_plan_nodes(
    state: UnifiedPlanExecutionState,
    nodes: Iterable[UnifiedPlanNode],
) -> UnifiedPlanExecutionState:
    """仅追加新节点；既有节点和终态不可被 patch 修改。"""

    additions = tuple(nodes)
    if not additions:
        raise ValueError("PLAN_APPEND_EMPTY")
    existing = {node.id for node in state.nodes}
    addition_ids = {node.id for node in additions}
    if len(addition_ids) != len(additions) or existing & addition_ids:
        raise ValueError("PLAN_APPEND_NODE_CONFLICT")
    combined = (*state.nodes, *additions)
    batches = _execution_batches(combined)
    task_states = dict(state.task_states)
    task_states.update({node.id: UnifiedPlanTaskState() for node in additions})
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "nodes": combined,
            "execution_batches": batches,
            "task_states": task_states,
        }
    )


def transition_plan_node(
    state: UnifiedPlanExecutionState,
    node_id: str,
    status: PlanNodeExecutionStatus,
    *,
    tool_call_id: str | None = None,
    evidence_ids: Sequence[str] = (),
    error_code: str | None = None,
) -> UnifiedPlanExecutionState:
    """统一更新节点状态，并拒绝已成功节点被重新执行。"""

    current = state.task_states.get(node_id)
    if current is None:
        raise ValueError("PLAN_TASK_STATE_UNKNOWN")
    if (
        current.status is PlanNodeExecutionStatus.SUCCEEDED
        and status is not current.status
    ):
        raise ValueError("PLAN_SUCCEEDED_TASK_IMMUTABLE")
    attempt = current.attempt
    if status is PlanNodeExecutionStatus.RUNNING:
        attempt += 1
    updated = current.model_copy(
        update={
            "status": status,
            "attempt": attempt,
            "tool_call_id": tool_call_id or current.tool_call_id,
            "evidence_ids": tuple(evidence_ids) or current.evidence_ids,
            "error_code": error_code,
        }
    )
    task_states = dict(state.task_states)
    task_states[node_id] = updated
    return state.model_copy(update={"task_states": task_states})


def load_plan_execution_state(
    payload: Mapping[str, Any] | None,
) -> UnifiedPlanExecutionState | None:
    if payload is None:
        return None
    # 旧快照中的模式与计划形态只用于历史兼容，不能继续进入规范执行状态。
    normalized = dict(payload)
    normalized.pop("mode", None)
    normalized.pop("shape", None)
    normalized.pop("allow_append", None)
    if "revision" not in normalized and "version" in normalized:
        normalized["revision"] = normalized.pop("version")
    raw_nodes = normalized.get("nodes")
    if isinstance(raw_nodes, (list, tuple)):
        normalized["nodes"] = [
            {**node, "source": "initial"}
            if isinstance(node, dict) and node.get("source") == "static"
            else node
            for node in raw_nodes
        ]
    return UnifiedPlanExecutionState.model_validate(normalized)


__all__ = [
    "PLAN_EXECUTION_STATE_KEY",
    "PlanNodeExecutionStatus",
    "UnifiedPlanExecutionState",
    "UnifiedPlanNode",
    "UnifiedPlanTaskState",
    "append_plan_nodes",
    "build_analysis_plan_execution_state",
    "build_plan_execution_state",
    "load_plan_execution_state",
    "transition_plan_node",
]
