"""根据完整执行需求确定性生成 PLAN 分析计划。"""

from __future__ import annotations

from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    AnalysisPlanStatus,
    AnalysisTask,
    ComputeDerivation,
    ComputeOperation,
    ComputeTask,
    PlanEdge,
    PlanValidation,
    PresentationHint,
    QueryTask,
)
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    CalculationRequirement,
    ExecutionRequirement,
    query_requirement_to_spec,
)
from apps.chatbi.services.planning.plan_validation import validate_analysis_plan


class AnalysisPlanner:
    """执行需求是唯一输入，规划阶段不再调用模型或读取旧语义状态。"""

    def __init__(self, *, max_query_tasks: int = 5) -> None:
        if max_query_tasks <= 0:
            raise ValueError("PLAN_MAX_QUERY_TASKS_INVALID")
        self._max_query_tasks = max_query_tasks

    def plan(
        self,
        *,
        plan_id: str,
        requirement: ExecutionRequirement,
        dataset_id: int,
    ) -> AnalysisPlan:
        """把完整执行需求投影为唯一 DAG，不允许补充或修改业务语义。"""

        requirement.require_ready("plan")
        query_tasks = tuple(
            QueryTask(
                id=f"q:{item.id}",
                source_requirement_id=item.id,
                spec=query_requirement_to_spec(item, dataset_id=dataset_id),
            )
            for item in requirement.query_requirements
        )
        node_ids = {
            **{item.id: f"q:{item.id}" for item in requirement.query_requirements},
            **{item.id: f"c:{item.id}" for item in requirement.post_calculations},
        }
        compute_tasks = tuple(
            self._compute_task(item, node_ids) for item in requirement.post_calculations
        )
        tasks: tuple[AnalysisTask, ...] = (*query_tasks, *compute_tasks)
        result_contract = requirement.result_contract
        primary_requirement_id = (
            result_contract.primary_requirement_id
            if result_contract is not None
            else _primary_requirement_id(requirement)
        )
        supporting_requirement_ids = (
            result_contract.supporting_requirement_ids
            if result_contract is not None
            else ()
        )
        ordered_requirement_ids = (
            result_contract.ordered_requirement_ids
            if result_contract is not None
            else ()
        )
        plan = AnalysisPlan(
            id=plan_id,
            tasks=tasks,
            edges=tuple(
                PlanEdge(source=input_id, target=task.id)
                for task in compute_tasks
                for input_id in task.inputs
            ),
            presentation=PresentationHint(
                primary_result=node_ids[primary_requirement_id],
                supporting_results=tuple(
                    node_ids[item] for item in supporting_requirement_ids
                ),
                ordered_results=tuple(
                    node_ids[item] for item in ordered_requirement_ids
                ),
                completion_policy=(
                    result_contract.completion_policy
                    if result_contract is not None
                    else "require_primary"
                ),
            ),
            validation=PlanValidation(
                status=AnalysisPlanStatus.DRAFT,
                reports=(
                    {
                        "planner_source": "rule",
                        "template": _template_name(requirement),
                        **(
                            {
                                "decomposer": requirement.decomposition.model_dump(
                                    mode="json"
                                )
                            }
                            if requirement.decomposition is not None
                            else {}
                        ),
                    },
                ),
            ),
        )
        validation = validate_analysis_plan(
            plan,
            max_query_tasks=self._max_query_tasks,
        )
        return plan.model_copy(
            update={
                "validation": validation.model_copy(
                    update={"reports": (*plan.validation.reports, *validation.reports)}
                )
            }
        )

    @staticmethod
    def _compute_task(
        calculation: CalculationRequirement,
        node_ids: dict[str, str],
    ) -> ComputeTask:
        """计算节点只引用执行需求，不在规划阶段解释自然语言。"""

        options = dict(calculation.options)
        if calculation.value_columns:
            options["value_columns"] = list(calculation.value_columns)
        derive = tuple(
            ComputeDerivation.model_validate(item) for item in calculation.derive
        )
        for requirement_id, node_id in node_ids.items():
            derive = tuple(
                item.model_copy(
                    update={
                        "expr": item.expr.replace(
                            f"{requirement_id}.",
                            f'"{node_id}".',
                        )
                    }
                )
                for item in derive
            )
        return ComputeTask(
            id=node_ids[calculation.id],
            source_calculation_id=calculation.id,
            operation=ComputeOperation(calculation.type.value),
            inputs=tuple(node_ids[input_id] for input_id in calculation.inputs),
            join_on=calculation.join_keys,
            derive=derive,
            options=options,
        )


def _primary_requirement_id(requirement: ExecutionRequirement) -> str:
    """最终结果必须是唯一叶子，禁止默认取第一个查询结果。"""

    all_ids = {
        *(item.id for item in requirement.query_requirements),
        *(item.id for item in requirement.post_calculations),
    }
    consumed = {
        input_id for item in requirement.post_calculations for input_id in item.inputs
    }
    leaves = all_ids - consumed
    if len(leaves) != 1:
        raise ValueError("PLAN_PRIMARY_RESULT_NOT_UNIQUE")
    return next(iter(leaves))


def _template_name(requirement: ExecutionRequirement) -> str:
    """模板只用于审计，DAG 仍由统一依赖契约生成。"""

    if requirement.result_contract is not None and (
        requirement.result_contract.analysis_type != "standard"
    ):
        return requirement.result_contract.analysis_type
    operations = tuple(item.type for item in requirement.post_calculations)
    if len(operations) == 1:
        return {
            CalculationOperation.MERGE: "cross_query_merge",
            CalculationOperation.DIFFERENCE: "period_difference",
            CalculationOperation.GROWTH_RATE: "growth_rate",
            CalculationOperation.SHARE: "share",
            CalculationOperation.RATIO: "ratio",
            CalculationOperation.TOPN_OTHER: "topn_other",
            CalculationOperation.PIVOT: "pivot",
            CalculationOperation.EXPR: "expression",
            CalculationOperation.CONTRIBUTION: "fixed_attribution",
        }[operations[0]]
    return "composed_calculations"


__all__ = ["AnalysisPlanner"]
