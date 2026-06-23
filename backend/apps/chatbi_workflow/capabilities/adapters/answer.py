from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from apps.chatbi_workflow.schemas.v1 import AnswerOutput, FinalReplyOutput


@dataclass(frozen=True)
class AnswerGenerationPrompt:
    """回复生成模型提示词。"""

    system_prompt: str
    user_prompt: str


class AnswerModelClient(Protocol):
    """回复生成模型客户端协议。"""

    def __call__(self, prompt: AnswerGenerationPrompt) -> str: ...


def build_answer_generation_prompt(
    mode: str,
    question: str,
    variables: dict[str, Any],
) -> AnswerGenerationPrompt:
    """构造稳定 JSON 输出的回复提示词。"""

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
- mode=generate 时，基于 variables 中已有结果生成回答；如果缺少结果，说明暂时无法生成完整回答。
- warnings 必须是字符串数组。
- render_type 默认使用 text。
- citations 必须是对象数组，没有引用时返回空数组。
""".strip()
    user_payload = {
        "mode": mode,
        "question": question,
        "variables": variables,
    }
    user_prompt = "请生成 ChatBI 回复，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return AnswerGenerationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


class DefaultAnswerModelClient:
    """默认回复模型客户端，复用项目已有 LLM 配置。"""

    def __init__(self) -> None:
        self._llm = None

    def __call__(self, prompt: AnswerGenerationPrompt) -> str:
        llm = self._get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=prompt.system_prompt),
                HumanMessage(content=prompt.user_prompt),
            ]
        )
        return str(getattr(response, "content", response) or "")

    def _get_llm(self):
        if self._llm is None:
            from apps.ai_model.model_factory import LLMFactory, get_default_config

            # 默认模型配置依赖异步解密逻辑；在已有事件循环中降级交给 adapter 处理。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError("answer model cannot be loaded inside a running event loop")
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


class AnswerAdapter:
    """ChatBI v1 回复节点真实能力适配器。"""

    def __init__(self, model_client: AnswerModelClient | None = None) -> None:
        self._model_client = model_client or DefaultAnswerModelClient()

    def reject(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成拒绝回复，模型不可用时返回稳定安全文案。"""

        return self._generate_with_model(
            "reject",
            request,
            fallback=self._answer_dump("当前问题无法在权限范围内回答。", ["answer_reject_fallback"]),
            parse_warning="answer_reject_parse_failed",
            model_warning="answer_reject_model_failed",
        )

    def chitchat(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成闲聊回复，模型不可用时返回固定引导文案。"""

        return self._generate_with_model(
            "chitchat",
            request,
            fallback=self._answer_dump("你好，我可以帮你分析业务数据问题。", ["answer_chitchat_fallback"]),
            parse_warning="answer_chitchat_parse_failed",
            model_warning="answer_chitchat_model_failed",
        )

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成业务回答，模型不可用时返回稳定降级文案。"""

        return self._generate_with_model(
            "generate",
            request,
            fallback=self._answer_dump("暂时无法生成完整回答，请稍后重试。", ["answer_generation_model_failed"]),
            parse_warning="answer_generation_parse_failed",
            model_warning="answer_generation_model_failed",
        )

    def compose(self, request: dict[str, Any]) -> dict[str, Any]:
        """本地合成最终回复，保持前端响应契约稳定。"""

        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        answer = variables.get("answer") if isinstance(variables.get("answer"), dict) else {}
        recommendations = variables.get("recommendations") if isinstance(variables.get("recommendations"), dict) else {}
        image_profile = variables.get("image_profile") if isinstance(variables.get("image_profile"), dict) else {}
        final_answer = str(answer.get("answer") or "暂时无法生成完整回答，请稍后重试。")
        return FinalReplyOutput(
            final_answer=final_answer,
            recommendations=list(recommendations.get("questions") or []),
            chart=image_profile,
            metadata={"source": "real_chatbi_v1"},
        ).model_dump(mode="json")

    def _generate_with_model(
        self,
        mode: str,
        request: dict[str, Any],
        fallback: dict[str, Any],
        parse_warning: str,
        model_warning: str,
    ) -> dict[str, Any]:
        raw_request = request.get("request", {})
        question = str(raw_request.get("question") or "").strip()
        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        prompt = build_answer_generation_prompt(mode=mode, question=question, variables=variables)
        try:
            model_text = self._model_client(prompt)
        except Exception:
            return self._fallback_with_warning(fallback, model_warning)
        try:
            payload = self._extract_json_object(model_text)
            output = AnswerOutput.model_validate(payload)
        except Exception:
            return self._fallback_with_warning(fallback, parse_warning)
        return output.model_dump(mode="json")

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        """从模型回复中提取第一个 JSON 对象。"""

        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("answer JSON object not found")

    @staticmethod
    def _answer_dump(answer: str, warnings: list[str]) -> dict[str, Any]:
        return AnswerOutput(
            answer=answer,
            warnings=warnings,
            render_type="text",
            citations=[],
        ).model_dump(mode="json")

    @staticmethod
    def _fallback_with_warning(fallback: dict[str, Any], warning: str) -> dict[str, Any]:
        result = dict(fallback)
        result["warnings"] = [warning]
        return result
