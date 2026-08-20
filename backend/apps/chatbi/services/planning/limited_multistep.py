"""有限多步任务的一次受限模型分解与确定性校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from apps.chatbi.errors import LimitedMultiStepDecompositionError, QuestionModelError
from apps.chatbi.models.dto.execution_requirement import (
    CalculationOperation,
    ExecutionDecompositionAudit,
    ExecutionRequirementDraft,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.services.planning.limited_multistep_rules import (
    LIMITED_MULTISTEP_PROMPT_VERSION,
)
from apps.chatbi.services.planning.ports import LimitedMultiStepPromptBuilder
from apps.chatbi.services.understanding.model_invocation import StructuredModelService


@dataclass(frozen=True, slots=True)
class LimitedMultiStepBudget:
    """有限多步模型和校验器共享的硬预算。"""

    max_query_count: int = 6
    max_calculation_count: int = 6
    max_total_nodes: int = 10
    max_dag_depth: int = 4


@dataclass(frozen=True, slots=True)
class LimitedMultiStepDecompositionResult:
    """通过校验的草案及模型调用审计信息。"""

    draft: ExecutionRequirementDraft
    audit: ExecutionDecompositionAudit


class LimitedMultiStepDecomposer:
    """调用一次模型分解执行需求，结构错误时最多修复一次。"""

    def __init__(
        self,
        model_service: StructuredModelService,
        prompt_builder: LimitedMultiStepPromptBuilder,
        *,
        budget: LimitedMultiStepBudget | None = None,
    ) -> None:
        if model_service is None:
            raise ValueError("LIMITED_MULTISTEP_MODEL_SERVICE_REQUIRED")
        if prompt_builder is None:
            raise ValueError("LIMITED_MULTISTEP_PROMPT_BUILDER_REQUIRED")
        self._model_service = model_service
        self._prompt_builder = prompt_builder
        self._budget = budget or LimitedMultiStepBudget()
        if min(
            self._budget.max_query_count,
            self._budget.max_calculation_count,
            self._budget.max_total_nodes,
            self._budget.max_dag_depth,
        ) <= 0:
            raise ValueError("LIMITED_MULTISTEP_BUDGET_INVALID")

    def decompose(
        self,
        *,
        objective: str,
        available_metrics: list[dict[str, Any]],
        available_dimensions: list[dict[str, Any]],
        time_roles: tuple[str, ...],
        requested_outputs: tuple[str, ...],
        allowed_operations: tuple[CalculationOperation, ...],
    ) -> LimitedMultiStepDecompositionResult:
        """生成并校验草案；模型始终看不到 SQL、字段和查询结果。"""

        context = {
            "objective": objective,
            "available_metrics": available_metrics,
            "available_dimensions": available_dimensions,
            "time_roles": list(time_roles),
            "requested_outputs": list(requested_outputs),
            "allowed_operations": [item.value for item in allowed_operations],
            "budget": {
                "max_query_count": self._budget.max_query_count,
                "max_calculation_count": self._budget.max_calculation_count,
                "max_total_nodes": self._budget.max_total_nodes,
                "max_dag_depth": self._budget.max_dag_depth,
            },
        }
        system_prompt, user_prompt = self._prompt_builder.build(context)
        first_payload = self._invoke(system_prompt, user_prompt)
        first_errors = self._validation_errors(
            first_payload,
            metric_refs={str(item["ref"]) for item in available_metrics},
            dimension_refs={str(item["ref"]) for item in available_dimensions},
            time_roles=set(time_roles),
            allowed_operations=set(allowed_operations),
        )
        if not first_errors:
            return LimitedMultiStepDecompositionResult(
                draft=ExecutionRequirementDraft.model_validate(first_payload),
                audit=ExecutionDecompositionAudit(
                    model=self._model_service.model_name,
                    prompt_version=LIMITED_MULTISTEP_PROMPT_VERSION,
                    attempts=1,
                    repaired=False,
                ),
            )

        system_prompt, user_prompt = self._prompt_builder.build_repair(
            context,
            first_payload,
            first_errors,
        )
        repaired_payload = self._invoke(system_prompt, user_prompt)
        repaired_errors = self._validation_errors(
            repaired_payload,
            metric_refs={str(item["ref"]) for item in available_metrics},
            dimension_refs={str(item["ref"]) for item in available_dimensions},
            time_roles=set(time_roles),
            allowed_operations=set(allowed_operations),
        )
        if repaired_errors:
            raise LimitedMultiStepDecompositionError(
                LimitedMultiStepDecompositionError.OUTPUT_INVALID,
                details={"attempts": 2, "errors": repaired_errors},
            )
        return LimitedMultiStepDecompositionResult(
            draft=ExecutionRequirementDraft.model_validate(repaired_payload),
            audit=ExecutionDecompositionAudit(
                model=self._model_service.model_name,
                prompt_version=LIMITED_MULTISTEP_PROMPT_VERSION,
                attempts=2,
                repaired=True,
            ),
        )

    def _invoke(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        try:
            result = self._model_service.invoke(
                QuestionModelInvocationData(
                    stage="limited_multistep_decomposition",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    json_mode=QuestionModelJSONMode.STRICT,
                )
            )
        except QuestionModelError as exc:
            raise LimitedMultiStepDecompositionError(
                LimitedMultiStepDecompositionError.MODEL_CALL_FAILED,
            ) from exc
        return dict(result.payload)

    def _validation_errors(
        self,
        payload: dict[str, Any],
        *,
        metric_refs: set[str],
        dimension_refs: set[str],
        time_roles: set[str],
        allowed_operations: set[CalculationOperation],
    ) -> list[dict[str, Any]]:
        try:
            draft = ExecutionRequirementDraft.model_validate(payload)
        except ValidationError as exc:
            return cast(
                list[dict[str, Any]],
                json.loads(
                    json.dumps(
                        exc.errors(include_url=False),
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            )

        errors: list[dict[str, Any]] = []
        if draft.result_contract.analysis_type != "limited_multistep":
            errors.append(
                {
                    "type": "LIMITED_MULTISTEP_RESULT_ANALYSIS_TYPE_INVALID",
                    "loc": ("result_contract", "analysis_type"),
                    "msg": "结果契约分析类型必须为 limited_multistep",
                }
            )
        query_count = len(draft.query_requirements)
        calculation_count = len(draft.post_calculations)
        if (
            query_count > self._budget.max_query_count
            or calculation_count > self._budget.max_calculation_count
            or query_count + calculation_count > self._budget.max_total_nodes
            or _draft_depth(draft) > self._budget.max_dag_depth
        ):
            errors.append(
                {
                    "type": LimitedMultiStepDecompositionError.BUDGET_EXCEEDED,
                    "loc": ("budget",),
                    "msg": "草案超过有限多步预算",
                }
            )
        for index, query in enumerate(draft.query_requirements):
            unknown_metrics = sorted(set(query.metric_refs) - metric_refs)
            unknown_dimensions = sorted(set(query.dimension_refs) - dimension_refs)
            if unknown_metrics or unknown_dimensions:
                errors.append(
                    {
                        "type": LimitedMultiStepDecompositionError.ASSET_REF_NOT_ALLOWED,
                        "loc": ("query_requirements", index),
                        "msg": "查询引用了未授权资产",
                        "ctx": {
                            "metric_refs": unknown_metrics,
                            "dimension_refs": unknown_dimensions,
                        },
                    }
                )
            if query.time_role not in time_roles:
                errors.append(
                    {
                        "type": LimitedMultiStepDecompositionError.TIME_ROLE_NOT_ALLOWED,
                        "loc": ("query_requirements", index, "time_role"),
                        "msg": "查询引用了未授权时间角色",
                    }
                )
        for index, calculation in enumerate(draft.post_calculations):
            refs = {
                *calculation.metric_refs,
                *(
                    (calculation.numerator_ref,)
                    if calculation.numerator_ref is not None
                    else ()
                ),
                *(
                    (calculation.denominator_ref,)
                    if calculation.denominator_ref is not None
                    else ()
                ),
            }
            dimensions = {
                *calculation.dimension_refs,
                *calculation.index_dimension_refs,
                *(
                    (calculation.column_dimension_ref,)
                    if calculation.column_dimension_ref is not None
                    else ()
                ),
            }
            if not refs <= metric_refs or not dimensions <= dimension_refs:
                errors.append(
                    {
                        "type": LimitedMultiStepDecompositionError.ASSET_REF_NOT_ALLOWED,
                        "loc": ("post_calculations", index),
                        "msg": "计算引用了未授权资产",
                    }
                )
            if calculation.type not in allowed_operations:
                errors.append(
                    {
                        "type": LimitedMultiStepDecompositionError.OPERATION_NOT_ALLOWED,
                        "loc": ("post_calculations", index, "type"),
                        "msg": "计算操作不在允许范围内",
                    }
                )
        return errors


def _draft_depth(draft: ExecutionRequirementDraft) -> int:
    """计算草案 DAG 深度，查询节点深度为一。"""

    dependencies = {item.id: item.inputs for item in draft.post_calculations}
    depths: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id not in dependencies:
            return 1
        if node_id not in depths:
            depths[node_id] = 1 + max(depth(item) for item in dependencies[node_id])
        return depths[node_id]

    return max(depth(item.id) for item in draft.post_calculations)


__all__ = [
    "LimitedMultiStepBudget",
    "LimitedMultiStepDecomposer",
    "LimitedMultiStepDecompositionResult",
]
