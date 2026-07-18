"""AI 模型运行时配置服务。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from apps.ai_model.errors import (
    AIModelConfigInvalidError,
    AIModelNotFoundError,
    DefaultAIModelNotConfiguredError,
)
from apps.ai_model.models.dto import LLMConfig, StoredAIModelConfig
from apps.ai_model.repository import AIModelConfigRepository
from apps.ai_model.services.model_config_rules import build_runtime_params

SecretDecryptor = Callable[[str], Awaitable[str]]


class AIModelRuntimeConfigService:
    def __init__(
        self,
        repository: AIModelConfigRepository,
        decrypt_secret: SecretDecryptor,
    ) -> None:
        self._repository = repository
        self._decrypt_secret = decrypt_secret

    async def resolve(self, model_id: int | None = None) -> LLMConfig:
        """解析指定模型或默认模型；指定 ID 不存在时不回退默认模型。"""

        stored = self._resolve_stored_config(model_id)
        api_domain = stored.api_domain
        api_key = stored.api_key
        if not api_domain.startswith("http"):
            api_domain = await self._decrypt_secret(api_domain)
            if api_key:
                api_key = await self._decrypt_secret(api_key)

        return LLMConfig(
            model_id=stored.model_id,
            model_type="openai" if stored.protocol == 1 else "vllm",
            model_name=stored.base_model,
            api_key=api_key,
            api_base_url=api_domain,
            additional_params=self._parse_additional_params(stored.config),
        )

    def _resolve_stored_config(
        self,
        model_id: int | None,
    ) -> StoredAIModelConfig:
        if model_id is not None:
            stored = self._repository.get_by_id(model_id)
            if stored is None:
                raise AIModelNotFoundError(model_id)
            return stored

        stored = self._repository.get_default()
        if stored is None:
            raise DefaultAIModelNotConfiguredError()
        return stored

    @staticmethod
    def _parse_additional_params(config: str | None) -> dict[str, Any]:
        if not config:
            return {}
        try:
            raw_items = json.loads(config)
        except json.JSONDecodeError as exc:
            raise AIModelConfigInvalidError("JSON_FORMAT") from exc
        if not isinstance(raw_items, list):
            raise AIModelConfigInvalidError("ROOT_MUST_BE_LIST")
        if not all(isinstance(item, dict) for item in raw_items):
            invalid_index = next(
                index
                for index, item in enumerate(raw_items, start=1)
                if not isinstance(item, dict)
            )
            raise AIModelConfigInvalidError(f"ITEM_NOT_OBJECT:{invalid_index}")

        return build_runtime_params(raw_items)
