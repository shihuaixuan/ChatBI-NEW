"""ChatBI 结构化回答生成服务。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from apps.chatbi.errors import QuestionModelCallError, QuestionModelError
from apps.chatbi.models.dto.answer_generation import (
    AnswerGenerationData,
    AnswerGenerationMode,
    AnswerGenerationPrompt,
    AnswerGenerationResult,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
    QuestionModelResponse,
)
from apps.chatbi.services.understanding.model_invocation import (
    StructuredModelService as QuestionModelService,
)


class AnswerModelClient(Protocol):
    """兼容 Graph 既有可调用回答模型的端口。"""

    def __call__(self, prompt: AnswerGenerationPrompt) -> str: ...


class CallableAnswerModelClient:
    """把既有可调用回答模型适配到统一结构化模型端口。"""

    def __init__(self, client: AnswerModelClient) -> None:
        self._client = client

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        content = self._client(
            AnswerGenerationPrompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        )
        return QuestionModelResponse(content=content)


class AnswerGenerationService:
    """调用统一模型边界并输出稳定回答或明确降级结果。"""

    def __init__(self, question_model_service: QuestionModelService) -> None:
        self._question_model_service = question_model_service

    def generate(self, data: AnswerGenerationData) -> AnswerGenerationResult:
        prompt = build_answer_generation_prompt(
            mode=data.mode,
            question=data.question,
            projection=data.projection,
        )
        fallback, model_warning, parse_warning = _fallback_contract(data)
        try:
            payload = self._question_model_service.invoke(
                QuestionModelInvocationData(
                    stage=f"answer_{data.mode}",
                    system_prompt=prompt.system_prompt,
                    user_prompt=prompt.user_prompt,
                    json_mode=QuestionModelJSONMode.EXTRACT_OBJECT,
                )
            ).payload
        except QuestionModelCallError:
            return fallback.model_copy(update={"warnings": [model_warning]})
        except QuestionModelError:
            return fallback.model_copy(update={"warnings": [parse_warning]})
        try:
            return AnswerGenerationResult.model_validate(payload)
        except ValidationError:
            return fallback.model_copy(update={"warnings": [parse_warning]})


def build_answer_generation_prompt(
    mode: AnswerGenerationMode,
    question: str,
    projection: dict[str, Any],
) -> AnswerGenerationPrompt:
    """构造稳定 JSON 输出的回答提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的回复生成器，只生成用户可读回复，不要输出 Markdown 代码块，不要泄露内部变量、trace、SQL 原文或权限规则细节。

你必须只输出一个 JSON 对象，不能输出前后缀文本。JSON 字段如下：
{
  "answer": "给用户看的简洁中文回复",
  "warnings": [],
  "render_type": "text",
  "citations": []
}

回复要求：
- mode=reject 时，只说明当前问题无法处理或不在权限范围内，不提供绕过方法。
- mode=chitchat 时，简短回应，并引导用户提出业务数据分析问题。
- mode=generate 时，基于 projection 中已有结果生成回答；如果缺少结果，说明暂时无法生成完整回答。
- 若 projection.plan.status 为 infeasible，或 infeasible_reason / issues 说明维度与指标不兼容，必须把原因和建议如实转述给用户，不要编造查询结果。
- warnings 必须是字符串数组。
- render_type 默认使用 text。
- citations 必须是对象数组，没有引用时返回空数组。
""".strip()
    user_prompt = "请生成 ChatBI 回复，并严格返回 JSON：\n" + json.dumps(
        {
            "mode": mode,
            "question": question,
            "projection": projection,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return AnswerGenerationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def _fallback_contract(
    data: AnswerGenerationData,
) -> tuple[AnswerGenerationResult, str, str]:
    if data.mode == "reject":
        return (
            _fallback_result("当前问题无法在权限范围内回答。"),
            "answer_reject_model_failed",
            "answer_reject_parse_failed",
        )
    if data.mode == "chitchat":
        return (
            _fallback_result("你好，我可以帮你分析业务数据问题。"),
            "answer_chitchat_model_failed",
            "answer_chitchat_parse_failed",
        )

    fallback_text = "暂时无法生成完整回答，请稍后重试。"
    model_warning = "answer_generation_model_failed"
    node_failure = data.projection.get("node_failure")
    if isinstance(node_failure, dict) and node_failure.get("error_code"):
        fallback_text = (
            f"本次查询未能完成（{node_failure.get('error_code')}），请调整问题后重试。"
        )
        model_warning = "answer_generation_degraded"
    return (
        _fallback_result(fallback_text),
        model_warning,
        "answer_generation_parse_failed",
    )


def _fallback_result(answer: str) -> AnswerGenerationResult:
    return AnswerGenerationResult(
        answer=answer,
        warnings=[],
        render_type="text",
        citations=[],
    )


__all__ = [
    "AnswerGenerationService",
    "AnswerModelClient",
    "CallableAnswerModelClient",
    "build_answer_generation_prompt",
]
