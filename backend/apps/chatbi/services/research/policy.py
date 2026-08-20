"""Research Policy 的单轮结构化模型决策。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from apps.chatbi.errors import QuestionModelError, ResearchExecutionError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.models.dto.research import (
    EvidenceSnapshot,
    ResearchActionType,
    ResearchPolicyDecision,
    ResearchRequirement,
    ResearchState,
)
from apps.chatbi.services.research.ports import ResearchPolicyPromptBuilder
from apps.chatbi.services.understanding.model_invocation import StructuredModelService

_RESEARCH_POLICY_ACTIONS = {
    ResearchActionType.BREAKDOWN,
    ResearchActionType.FILTER_FROM_RESULT,
    ResearchActionType.DRILLDOWN,
    ResearchActionType.CONTRIBUTION,
    ResearchActionType.VALIDATE_HYPOTHESIS,
    ResearchActionType.FINISH,
}


class ResearchPolicy:
    """调用一次模型并校验第三阶段允许的决策边界。"""

    def __init__(
        self,
        model_service: StructuredModelService,
        prompt_builder: ResearchPolicyPromptBuilder,
    ) -> None:
        if model_service is None:
            raise ValueError("RESEARCH_POLICY_MODEL_SERVICE_REQUIRED")
        if prompt_builder is None:
            raise ValueError("RESEARCH_POLICY_PROMPT_BUILDER_REQUIRED")
        self._model_service = model_service
        self._prompt_builder = prompt_builder

    def decide(
        self,
        *,
        requirement: ResearchRequirement,
        state: ResearchState,
        evidence: tuple[EvidenceSnapshot, ...],
        asset_catalog: tuple[dict[str, Any], ...],
    ) -> ResearchPolicyDecision:
        """模型只能读取逻辑资产、证据摘要和剩余预算。"""

        available_actions = tuple(
            item
            for item in requirement.allowed_actions
            if item in _RESEARCH_POLICY_ACTIONS
        )
        context = {
            "requirement": requirement.model_dump(mode="json"),
            "asset_catalog": list(asset_catalog),
            "state": state.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "remaining_budget": state.remaining_budget.model_dump(mode="json"),
            "available_actions": [item.value for item in available_actions],
        }
        system_prompt, user_prompt = self._prompt_builder.build(context)
        try:
            result = self._model_service.invoke(
                QuestionModelInvocationData(
                    stage="research_policy",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    json_mode=QuestionModelJSONMode.STRICT,
                )
            )
        except QuestionModelError as exc:
            raise ResearchExecutionError(
                ResearchExecutionError.MODEL_CALL_FAILED
            ) from exc
        try:
            decision = ResearchPolicyDecision.model_validate(result.payload)
        except ValidationError as exc:
            raise ResearchExecutionError(
                ResearchExecutionError.POLICY_OUTPUT_INVALID,
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        if decision.decision.type == "execute":
            actions = decision.decision.actions
            if len(actions) > requirement.budget.max_actions_per_iteration:
                raise ResearchExecutionError(
                    ResearchExecutionError.BUDGET_EXHAUSTED,
                    details={"reason": "RESEARCH_ACTIONS_PER_ITERATION_EXCEEDED"},
                )
            if any(action.type not in available_actions for action in actions):
                raise ResearchExecutionError(
                    ResearchExecutionError.ACTION_NOT_ALLOWED
                )
        return decision


__all__ = ["ResearchPolicy"]
