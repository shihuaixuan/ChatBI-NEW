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
    description: str = Field(min_length=1, max_length=1000)
    task_type: Literal["query", "compute", "inspect", "respond"]
    dependencies: tuple[str, ...] = ()
    tool_name: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(default=1, ge=1)
    gap_id: str | None = Field(default=None, min_length=1, max_length=128)
    arguments: dict[str, Any]
    output_type: Literal["evidence", "response"]
    expected_output: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_dependencies(self) -> UnifiedPlanNode:
        if self.id in self.dependencies:
            raise ValueError("PLAN_NODE_SELF_DEPENDENCY")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("PLAN_NODE_DEPENDENCY_DUPLICATED")
        if not self.arguments:
            raise ValueError("PLAN_NODE_ARGUMENTS_REQUIRED")
        allowed_tools = {
            "query": {"query", "query_semantic_data"},
            "compute": {"compute", "compute_evidence"},
            "inspect": {"inspect_evidence"},
            "respond": {"generate_response"},
        }
        if self.tool_name not in allowed_tools[self.task_type]:
            raise ValueError("PLAN_NODE_TOOL_TYPE_MISMATCH")
        expected_output_type = (
            "response" if self.task_type == "respond" else "evidence"
        )
        if self.output_type != expected_output_type:
            raise ValueError("PLAN_NODE_OUTPUT_TYPE_MISMATCH")
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
            if node.plan_revision > self.revision:
                raise ValueError("PLAN_NODE_REVISION_AHEAD")
        if self.nodes and max(node.plan_revision for node in self.nodes) != self.revision:
            raise ValueError("PLAN_NODE_REVISION_NOT_MATCHED")
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
            description=(
                f"执行计算任务 {task.id}"
                if isinstance(task, ComputeTask)
                else f"执行查询任务 {task.id}"
            ),
            task_type="compute" if isinstance(task, ComputeTask) else "query",
            dependencies=tuple(dependencies[task.id]),
            tool_name="compute" if isinstance(task, ComputeTask) else "query",
            plan_revision=plan.version,
            arguments=task.model_dump(mode="json"),
            output_type="evidence",
            expected_output=(
                "返回计算结果集"
                if isinstance(task, ComputeTask)
                else "返回查询结果集"
            ),
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


def ensure_analysis_plan_execution_state(
    payload: Mapping[str, Any] | None,
    plan: AnalysisPlan,
) -> UnifiedPlanExecutionState:
    """创建或校验 AnalysisPlan 对应的统一执行状态。"""

    expected = build_analysis_plan_execution_state(plan)
    existing = load_plan_execution_state(payload)
    if existing is None:
        return expected
    expected_nodes = tuple(
        (node.id, node.task_type, node.dependencies) for node in expected.nodes
    )
    existing_nodes = tuple(
        (node.id, node.task_type, node.dependencies) for node in existing.nodes
    )
    if existing.plan_id != expected.plan_id or existing_nodes != expected_nodes:
        raise ValueError("PLAN_EXECUTION_STATE_PLAN_MISMATCH")
    if existing.nodes == expected.nodes:
        return existing
    if any(
        task_state.status is not PlanNodeExecutionStatus.PENDING
        for task_state in existing.task_states.values()
    ):
        raise ValueError("PLAN_EXECUTION_STATE_PLAN_IMMUTABLE")
    # SQL 编译等确定性准备发生在执行前；只允许覆盖尚未启动节点的完整参数。
    return existing.model_copy(update={"nodes": expected.nodes})


def build_plan_execution_state(
    plan_id: str,
    nodes: Sequence[UnifiedPlanNode] = (),
) -> UnifiedPlanExecutionState:
    """建立统一可追加 DAG；空计划允许 Planner 根据当前上下文生成首轮节点。"""

    frozen_nodes = tuple(nodes)
    revision = max((node.plan_revision for node in frozen_nodes), default=1)
    return UnifiedPlanExecutionState(
        plan_id=plan_id,
        revision=revision,
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
    target_revision = 1 if not state.nodes else state.revision + 1
    revised_additions = tuple(
        node.model_copy(update={"plan_revision": target_revision})
        for node in additions
    )
    combined = (*state.nodes, *revised_additions)
    batches = _execution_batches(combined)
    task_states = dict(state.task_states)
    task_states.update(
        {node.id: UnifiedPlanTaskState() for node in revised_additions}
    )
    return state.model_copy(
        update={
            "revision": target_revision,
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
        current_revision = int(normalized.get("revision") or 1)
        migrated_nodes: list[Any] = []
        for node in raw_nodes:
            if not isinstance(node, dict) or "source" not in node:
                migrated_nodes.append(node)
                continue
            migrated = dict(node)
            migrated.pop("source", None)
            # 旧状态只有 initial/append 标记，无法还原准确规划轮次；统一归入
            # 快照当前 revision，保证恢复后不会伪造不存在的历史轮次。
            migrated["plan_revision"] = current_revision
            task_type = migrated.get("task_type")
            arguments = migrated.get("arguments")
            purpose = (
                arguments.get("purpose") if isinstance(arguments, dict) else None
            )
            migrated.setdefault(
                "description",
                str(purpose or migrated.get("id") or "历史计划步骤"),
            )
            migrated.setdefault(
                "output_type",
                "response" if task_type == "respond" else "evidence",
            )
            migrated.setdefault(
                "expected_output",
                "返回最终响应" if task_type == "respond" else "返回执行 Evidence",
            )
            migrated_nodes.append(migrated)
        normalized["nodes"] = migrated_nodes
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
    "ensure_analysis_plan_execution_state",
    "load_plan_execution_state",
    "transition_plan_node",
]
