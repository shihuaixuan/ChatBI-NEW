from __future__ import annotations

import asyncio

import pytest

from apps.ai_model.errors import (
    AIModelConfigInvalidError,
    AIModelNotFoundError,
    DefaultAIModelNotConfiguredError,
)
from apps.ai_model.models.dto import LLMConfig, StoredAIModelConfig
from apps.ai_model.services import AIModelRuntimeConfigService


class FakeAIModelConfigRepository:
    def __init__(
        self,
        *,
        models: dict[int, StoredAIModelConfig] | None = None,
        default: StoredAIModelConfig | None = None,
    ) -> None:
        self.models = models or {}
        self.default = default
        self.default_read_count = 0

    def get_by_id(self, model_id: int) -> StoredAIModelConfig | None:
        return self.models.get(model_id)

    def get_default(self) -> StoredAIModelConfig | None:
        self.default_read_count += 1
        return self.default


async def _identity_decrypt(value: str) -> str:
    return value


def _stored_config(**changes: object) -> StoredAIModelConfig:
    data: dict[str, object] = {
        "model_id": 7,
        "protocol": 1,
        "base_model": "gpt-test",
        "api_key": "plain-key",
        "api_domain": "https://example.test/v1",
        "config": '[{"key":"stop","val":"[\\"END\\"]"}]',
    }
    data.update(changes)
    return StoredAIModelConfig.model_validate(data)


def test_resolve_default_model_builds_stable_runtime_config() -> None:
    service = AIModelRuntimeConfigService(
        repository=FakeAIModelConfigRepository(default=_stored_config()),
        decrypt_secret=_identity_decrypt,
    )

    result = asyncio.run(service.resolve())

    assert result == LLMConfig(
        model_id=7,
        model_type="openai",
        model_name="gpt-test",
        api_key="plain-key",
        api_base_url="https://example.test/v1",
        additional_params={"stop": ["END"]},
    )
    assert hash(result) == hash(result.model_copy(deep=True))


def test_resolve_missing_custom_model_does_not_fallback_to_default() -> None:
    repository = FakeAIModelConfigRepository(default=_stored_config())
    service = AIModelRuntimeConfigService(repository, _identity_decrypt)

    with pytest.raises(AIModelNotFoundError, match="AI_MODEL_NOT_FOUND:99"):
        asyncio.run(service.resolve(99))

    assert repository.default_read_count == 0


def test_resolve_requires_configured_default_model() -> None:
    service = AIModelRuntimeConfigService(
        FakeAIModelConfigRepository(),
        _identity_decrypt,
    )

    with pytest.raises(
        DefaultAIModelNotConfiguredError,
        match="AI_MODEL_DEFAULT_NOT_CONFIGURED",
    ):
        asyncio.run(service.resolve())


def test_resolve_rejects_duplicate_config_keys() -> None:
    stored = _stored_config(
        config='[{"key":"temperature","val":"1"},'
        '{"key":"temperature","val":"2"}]'
    )
    service = AIModelRuntimeConfigService(
        FakeAIModelConfigRepository(default=stored),
        _identity_decrypt,
    )

    with pytest.raises(AIModelConfigInvalidError, match="DUPLICATE_KEY:temperature"):
        asyncio.run(service.resolve())


def test_resolve_decrypts_encrypted_endpoint_and_key_together() -> None:
    decrypted_values = {
        "encrypted-domain": "https://example.test/v1",
        "encrypted-key": "secret-key",
    }

    async def decrypt(value: str) -> str:
        return decrypted_values[value]

    stored = _stored_config(
        protocol=2,
        api_domain="encrypted-domain",
        api_key="encrypted-key",
        config=None,
    )
    service = AIModelRuntimeConfigService(
        FakeAIModelConfigRepository(models={7: stored}),
        decrypt,
    )

    result = asyncio.run(service.resolve(7))

    assert result.model_type == "vllm"
    assert result.api_base_url == "https://example.test/v1"
    assert result.api_key == "secret-key"
