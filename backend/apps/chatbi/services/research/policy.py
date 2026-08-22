"""Research Policy 的单轮结构化模型决策。"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ResearchPolicyResult:
    """单轮决策及其真实模型调用消耗。"""

    decision: ResearchPolicyDecision
    model_calls: int


class ResearchPolicy:
    """调用一次模型并校验允许的决策边界。"""

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

        return self.decide_with_usage(
            requirement=requirement,
            state=state,
            evidence=evidence,
            asset_catalog=asset_catalog,
            max_model_calls=state.remaining_budget.model_calls,
        ).decision

    def decide_with_usage(
        self,
        *,
        requirement: ResearchRequirement,
        state: ResearchState,
        evidence: tuple[EvidenceSnapshot, ...],
        asset_catalog: tuple[dict[str, Any], ...],
        max_model_calls: int,
    ) -> ResearchPolicyResult:
        """执行一次决策，结构修复也必须计入模型调用预算。"""

        if max_model_calls <= 0:
            raise ResearchExecutionError(ResearchExecutionError.BUDGET_EXHAUSTED)

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
        decision, validation_errors = self._invoke_and_validate(
            system_prompt, user_prompt
        )
        model_calls = 1
        if decision is None and max_model_calls >= 2:
            # 可恢复结构错误最多修复一次：附带校验错误重试，仍失败才拒绝。
            try:
                decision, validation_errors = self._invoke_and_validate(
                    system_prompt,
                    user_prompt
                    + "\n\n你上一次的输出未通过 JSON 结构校验，错误如下：\n"
                    + "\n".join(validation_errors)
                    + "\n请严格按照系统规则中的动作 JSON 结构输出修正后的完整决策，只输出 JSON。",
                )
            except ResearchExecutionError as exc:
                raise ResearchExecutionError(
                    exc.code,
                    details={**exc.details, "model_calls": 2},
                ) from exc
            model_calls += 1
        if decision is None:
            raise ResearchExecutionError(
                ResearchExecutionError.POLICY_OUTPUT_INVALID,
                details={
                    "reason": "RESEARCH_POLICY_REPAIR_FAILED",
                    "model_calls": model_calls,
                },
            )
        if decision.decision.type == "execute":
            actions = decision.decision.actions
            if len(actions) > requirement.budget.max_actions_per_iteration:
                raise ResearchExecutionError(
                    ResearchExecutionError.BUDGET_EXHAUSTED,
                    details={
                        "reason": "RESEARCH_ACTIONS_PER_ITERATION_EXCEEDED",
                        "model_calls": model_calls,
                    },
                )
            if any(action.type not in available_actions for action in actions):
                raise ResearchExecutionError(
                    ResearchExecutionError.ACTION_NOT_ALLOWED,
                    details={"model_calls": model_calls},
                )
        return ResearchPolicyResult(decision=decision, model_calls=model_calls)

    def _invoke_and_validate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[ResearchPolicyDecision | None, list[str]]:
        """调用一次模型并校验输出；失败时返回结构错误摘要供修复重试。"""

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
                ResearchExecutionError.MODEL_CALL_FAILED,
                details={"model_calls": 1},
            ) from exc
        try:
            decision = ResearchPolicyDecision.model_validate(result.payload)
        except ValidationError as exc:
            errors = [
                "字段 "
                + ".".join(str(item) for item in error.get("loc") or ())
                + ": "
                + str(error.get("msg"))
                for error in exc.errors(include_url=False)
            ]
            return None, errors
        return decision, []


__all__ = ["ResearchPolicy", "ResearchPolicyResult"]
