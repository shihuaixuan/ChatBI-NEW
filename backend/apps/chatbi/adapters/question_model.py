from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from apps.ai_model.model_factory import LLMFactory, get_default_config
from apps.ai_model.models.dto import LLMConfig
from apps.chatbi.models import QuestionModelResponse
from apps.chatbi.services.understanding import StructuredModelService
from common.core.config import settings


class LangChainQuestionModelClient:
    """使用系统默认模型执行问题理解结构化子任务。"""

    def __init__(
        self,
        *,
        reasoning_effort: str | None = None,
        question_defaults: bool = True,
        enforce_json: bool = False,
        max_retries: int | None = None,
    ) -> None:
        self._llm: BaseChatModel | None = None
        self._config: LLMConfig | None = None
        self._reasoning_effort = (
            settings.QUERY_UNDERSTANDING_REASONING_EFFORT
            if question_defaults and reasoning_effort is None
            else reasoning_effort
        )
        self._enforce_json = enforce_json
        self._max_retries = max_retries

    @property
    def model_name(self) -> str | None:
        """返回当前问题理解模型名称，不触发模型加载。"""

        return self._config.model_name if self._config is not None else None

    @property
    def provider_name(self) -> str | None:
        """返回模型服务商主机名，不记录完整 API 地址。"""

        if self._config is None or not self._config.api_base_url:
            return None
        return urlparse(self._config.api_base_url).hostname

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        response = self._get_llm().invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ],
            # 问题理解是结构抽取任务，固定低随机性以减少同问不同结构。
            temperature=0,
        )
        return QuestionModelResponse(
            content=_message_content_text(response),
            usage_metadata=dict(getattr(response, "usage_metadata", None) or {}),
        )

    def _get_llm(self) -> BaseChatModel:
        if self._llm is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError(
                    "question model cannot be loaded inside a running event loop"
                )
            timeout_ms = settings.QUERY_UNDERSTANDING_TIMEOUT_MS
            if timeout_ms <= 0:
                raise ValueError("QUERY_UNDERSTANDING_TIMEOUT_MS_INVALID")
            # 问题理解使用独立配置副本，避免修改 Agent 主模型或持久化模型配置。
            additional_params = dict(config.additional_params)
            # 结构化问题理解不设置固定输出上限，避免模型输出被 max_tokens 截断。
            additional_params.pop("max_tokens", None)
            if self._reasoning_effort is not None:
                additional_params["reasoning_effort"] = self._reasoning_effort
            if self._enforce_json:
                # R1 的模型端口必须先满足 JSON 传输协议，结构合法性仍由领域 DTO 校验。
                additional_params.setdefault("response_format", {"type": "json_object"})
            if self._max_retries is not None:
                if self._max_retries < 0:
                    raise ValueError("QUESTION_MODEL_MAX_RETRIES_INVALID")
                # R1 每个阶段受单次调用预算约束，禁止网络重试把一题拖成数分钟。
                additional_params["max_retries"] = self._max_retries
            additional_params["timeout"] = timeout_ms / 1000
            question_config = config.model_copy(
                update={"additional_params": additional_params}
            )
            self._config = question_config
            self._llm = LLMFactory.create_llm(question_config).llm
        return self._llm


def build_question_model_service(
    *,
    reasoning_effort: str | None = None,
    question_defaults: bool = True,
    enforce_json: bool = False,
    max_retries: int | None = None,
) -> StructuredModelService:
    """装配问题理解各阶段共享的默认模型客户端。"""

    return StructuredModelService(
        LangChainQuestionModelClient(
            reasoning_effort=reasoning_effort,
            question_defaults=question_defaults,
            enforce_json=enforce_json,
            max_retries=max_retries,
        )
    )


def _message_content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return ""


__all__ = [
    "LangChainQuestionModelClient",
    "build_question_model_service",
]
