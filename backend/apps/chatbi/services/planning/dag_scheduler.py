"""AnalysisPlan 的确定性 DAG 批次调度。"""

from __future__ import annotations

from enum import StrEnum

from apps.chatbi.models.dto.analysis_plan import AnalysisPlan, ComputeTask


class AnalysisTaskExecutionStatus(StrEnum):
    """计划节点在一次执行中的稳定状态。"""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED_DEPENDENCY = "SKIPPED_DEPENDENCY"
    CANCELLED = "CANCELLED"


def task_dependencies(plan: AnalysisPlan) -> dict[str, tuple[str, ...]]:
    """以 ComputeTask.inputs 为唯一依赖事实源。"""

    return {
        task.id: task.inputs if isinstance(task, ComputeTask) else ()
        for task in plan.tasks
    }


def build_execution_batches(plan: AnalysisPlan) -> tuple[tuple[str, ...], ...]:
    """按计划原始节点顺序生成可并行执行的拓扑批次。"""

    dependencies = task_dependencies(plan)
    remaining = [task.id for task in plan.tasks]
    completed: set[str] = set()
    batches: list[tuple[str, ...]] = []
    while remaining:
        ready = tuple(
            task_id
            for task_id in remaining
            if set(dependencies[task_id]).issubset(completed)
        )
        if not ready:
            raise ValueError("PLAN_COMPUTE_DAG_UNRESOLVED")
        batches.append(ready)
        completed.update(ready)
        ready_set = set(ready)
        remaining = [task_id for task_id in remaining if task_id not in ready_set]
    return tuple(batches)


__all__ = [
    "AnalysisTaskExecutionStatus",
    "build_execution_batches",
    "task_dependencies",
]
