"""AnalysisPlan 的结构和引用校验。"""

from __future__ import annotations

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    ComputeOperation,
    ComputeTask,
    PlanValidation,
    QueryTask,
)


class PlanValidationError(ValueError):
    """计划不能进入确定性执行阶段。"""

    def __init__(self, reason_codes: tuple[str, ...], message: str | None = None) -> None:
        self.reason_codes = reason_codes
        super().__init__(message or ",".join(reason_codes))


class AnalysisPlanValidator:
    """校验 DAG、节点上限和 QueryTask 编译证明状态。"""

    def __init__(self, *, max_query_tasks: int = 5) -> None:
        if max_query_tasks <= 0:
            raise ValueError("PLAN_MAX_QUERY_TASKS_INVALID")
        self._max_query_tasks = max_query_tasks

    def validate(
        self,
        plan: AnalysisPlan,
        *,
        require_proven: bool = False,
    ) -> PlanValidation:
        reasons: list[str] = []
        query_tasks = [task for task in plan.tasks if isinstance(task, QueryTask)]
        if len(query_tasks) > self._max_query_tasks:
            # 查询组数量是系统计划预算，不属于用户表达缺失，必须走计划拒答门。
            reasons.append("PLAN_QUERY_GROUP_LIMIT_EXCEEDED")
        reasons.extend(self._validate_edges(plan))
        if require_proven:
            reasons.extend(
                "QUERY_TASK_NOT_PROVEN"
                for task in query_tasks
                if task.compiled is None or not task.compiled.sql.strip()
            )
        reasons.extend(self._validate_compute_inputs(plan))
        status = AnalysisPlanStatus.PROVEN if not reasons and require_proven else (
            AnalysisPlanStatus.DRAFT if not reasons else AnalysisPlanStatus.REJECTED
        )
        return PlanValidation(
            status=status,
            reason_codes=tuple(dict.fromkeys(reasons)),
            reports=(
                {
                    "query_task_count": len(query_tasks),
                    "max_query_tasks": self._max_query_tasks,
                    "require_proven": require_proven,
                },
            ),
        )

    @staticmethod
    def _validate_edges(plan: AnalysisPlan) -> list[str]:
        known = {task.id for task in plan.tasks}
        adjacency: dict[str, list[str]] = {task_id: [] for task_id in known}
        reasons: list[str] = []
        for edge in plan.edges:
            if edge.source not in known or edge.target not in known:
                reasons.append("PLAN_EDGE_NODE_UNKNOWN")
                continue
            adjacency[edge.source].append(edge.target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                reasons.append("PLAN_DAG_CYCLE")
                return
            if node in visited:
                return
            visiting.add(node)
            for target in adjacency[node]:
                visit(target)
            visiting.remove(node)
            visited.add(node)

        for node in known:
            visit(node)
        return reasons

    @staticmethod
    def _validate_compute_inputs(plan: AnalysisPlan) -> list[str]:
        known = {task.id for task in plan.tasks}
        reasons: list[str] = []
        for task in plan.tasks:
            if isinstance(task, ComputeTask):
                if len(task.inputs) != len(set(task.inputs)):
                    reasons.append("COMPUTE_TASK_INPUTS_DUPLICATED")
                minimum_inputs = (
                    2
                    if task.operation in {
                        ComputeOperation.COMPARE,
                        ComputeOperation.GROWTH,
                    }
                    else 1
                )
                if len(task.inputs) < minimum_inputs:
                    reasons.append("COMPUTE_TASK_INPUTS_INSUFFICIENT")
                if any(item not in known for item in task.inputs):
                    reasons.append("COMPUTE_TASK_INPUT_UNKNOWN")
        return reasons


def validate_analysis_plan(
    plan: AnalysisPlan,
    *,
    max_query_tasks: int = 5,
    require_proven: bool = False,
) -> PlanValidation:
    """提供给编排器和测试使用的轻量函数入口。"""

    return AnalysisPlanValidator(max_query_tasks=max_query_tasks).validate(
        plan,
        require_proven=require_proven,
    )


__all__ = ["AnalysisPlanValidator", "PlanValidationError", "validate_analysis_plan"]
