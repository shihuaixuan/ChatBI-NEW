"""AI 模型管理服务。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from apps.ai_model.errors import (
    AIModelConfigInvalidError,
    AIModelDefaultCannotDeleteError,
    AIModelDefaultChangeRequiresEndpointError,
    AIModelNotFoundError,
)
from apps.ai_model.models.dto import (
    AiModelConfigItem,
    AIModelCreateData,
    AiModelCreator,
    AIModelDeleteResult,
    AiModelEditor,
    AiModelGridItem,
    AIModelRecord,
    AIModelUpdateData,
    LLMConfig,
)
from apps.ai_model.repository import AIModelManagementRepository
from apps.ai_model.services.model_config_rules import (
    build_runtime_params,
    normalize_config_items,
)
from common.utils.time import get_timestamp

SecretDecryptor = Callable[[str], Awaitable[str]]


class AIModelManagementService:
    def __init__(
        self,
        repository: AIModelManagementRepository,
        decrypt_secret: SecretDecryptor,
    ) -> None:
        self._repository = repository
        self._decrypt_secret = decrypt_secret

    def list_models(self, keyword: str | None = None) -> list[AiModelGridItem]:
        return [
            AiModelGridItem.model_validate(record.model_dump())
            for record in self._repository.list_models(keyword)
        ]

    async def get_model(self, model_id: int) -> AiModelEditor:
        record = self._require_model(model_id)
        api_domain = record.api_domain
        api_key = record.api_key
        if not api_domain.startswith("http"):
            api_domain = await self._decrypt_secret(api_domain)
            if api_key:
                api_key = await self._decrypt_secret(api_key)
        return AiModelEditor(
            id=record.id,
            name=record.name,
            model_type=record.model_type,
            base_model=record.base_model,
            supplier=record.supplier,
            protocol=record.protocol,
            default_model=record.default_model,
            api_domain=api_domain,
            api_key=api_key or "",
            config_list=self._deserialize_config(record.config),
        )

    def create_model(self, creator: AiModelCreator) -> AIModelRecord:
        return self._repository.create_model(
            AIModelCreateData(
                name=creator.name,
                model_type=creator.model_type,
                base_model=creator.base_model,
                supplier=creator.supplier,
                protocol=creator.protocol,
                default_model=creator.default_model,
                api_domain=creator.api_domain,
                api_key=creator.api_key,
                config=self._serialize_config(creator.config_list),
                create_time=get_timestamp(),
            )
        )

    def update_model(self, editor: AiModelEditor) -> AIModelRecord:
        current = self._require_model(editor.id)
        if editor.default_model != current.default_model:
            raise AIModelDefaultChangeRequiresEndpointError(editor.id)
        updated = self._repository.update_model(
            editor.id,
            AIModelUpdateData(
                name=editor.name,
                model_type=editor.model_type,
                base_model=editor.base_model,
                supplier=editor.supplier,
                protocol=editor.protocol,
                api_domain=editor.api_domain,
                api_key=editor.api_key,
                config=self._serialize_config(editor.config_list),
            ),
        )
        if updated is None:
            raise AIModelNotFoundError(editor.id)
        return updated

    def set_default(self, model_id: int) -> AIModelRecord:
        updated = self._repository.set_default(model_id)
        if updated is None:
            raise AIModelNotFoundError(model_id)
        return updated

    def delete_model(self, model_id: int) -> None:
        current = self._require_model(model_id)
        if current.default_model:
            raise AIModelDefaultCannotDeleteError(current.name)
        result = self._repository.delete_model(model_id)
        if result == AIModelDeleteResult.DEFAULT_MODEL:
            raise AIModelDefaultCannotDeleteError(current.name)
        if result == AIModelDeleteResult.NOT_FOUND:
            raise AIModelNotFoundError(model_id)

    def has_default(self) -> bool:
        return self._repository.has_default()

    @staticmethod
    def build_validation_config(info: AiModelCreator) -> LLMConfig:
        raw_items = [item.model_dump() for item in info.config_list]
        return LLMConfig(
            model_type="openai" if info.protocol == 1 else "vllm",
            model_name=info.base_model,
            api_key=info.api_key,
            api_base_url=info.api_domain,
            additional_params=build_runtime_params(raw_items),
        )

    def _require_model(self, model_id: int) -> AIModelRecord:
        record = self._repository.get_model(model_id)
        if record is None:
            raise AIModelNotFoundError(model_id)
        return record

    @staticmethod
    def _serialize_config(items: list[AiModelConfigItem]) -> str:
        normalized = normalize_config_items([item.model_dump() for item in items])
        return json.dumps(normalized, ensure_ascii=False)

    @staticmethod
    def _deserialize_config(config: str | None) -> list[AiModelConfigItem]:
        if not config:
            return []
        try:
            raw_items = json.loads(config)
        except json.JSONDecodeError as exc:
            raise AIModelConfigInvalidError("JSON_FORMAT") from exc
        if not isinstance(raw_items, list):
            raise AIModelConfigInvalidError("ROOT_MUST_BE_LIST")
        if not all(isinstance(item, dict) for item in raw_items):
            raise AIModelConfigInvalidError("ITEM_NOT_OBJECT")
        normalized = normalize_config_items(raw_items)
        try:
            return [AiModelConfigItem.model_validate(item) for item in normalized]
        except ValidationError as exc:
            raise AIModelConfigInvalidError("ITEM_SCHEMA") from exc
