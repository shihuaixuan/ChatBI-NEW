"""AI Model 公开运行时装配：默认/指定模型的一次性运行快照。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.chat_models.base import BaseChatModel

from apps.ai_model.model_factory import LLMFactory, get_default_config
from apps.ai_model.models.dto import LLMConfig


@dataclass(frozen=True, slots=True)
class LLMRuntime:
    """一次生成流程使用的模型配置与已装配客户端。"""

    config: LLMConfig
    llm: BaseChatModel


async def build_llm_runtime(
    model_id: str | int | None = None,
    *,
    no_reasoning: bool = False,
) -> LLMRuntime:
    """装配默认或指定模型的运行时客户端。

    `no_reasoning` 会从模型附加参数中移除 `enable_thinking`，保持既有关闭思考行为。
    """

    resolved_model_id = int(model_id) if model_id is not None else None
    config = await get_default_config(resolved_model_id)
    if no_reasoning and config.additional_params:
        extra_body = config.additional_params.get("extra_body")
        if isinstance(extra_body, dict):
            extra_body.pop("enable_thinking", None)

    llm_instance: Any = LLMFactory.create_llm(config)
    return LLMRuntime(config=config, llm=llm_instance.llm)


__all__ = ["LLMRuntime", "build_llm_runtime"]
