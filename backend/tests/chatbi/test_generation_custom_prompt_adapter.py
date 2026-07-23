from typing import cast

from sqlbot_xpack.custom_prompt.models.custom_prompt_model import (
    CustomPromptTypeEnum,
)
from sqlmodel import Session

from apps.chatbi.adapters import generation_custom_prompt as adapter_module
from apps.chatbi.adapters.generation_custom_prompt import (
    XPackGenerationCustomPromptClient,
)
from apps.chatbi.models import (
    GenerationCustomPromptQuery,
    GenerationCustomPromptType,
)


def test_xpack_adapter_maps_prompt_type_and_query_scope(monkeypatch):
    captured: dict[str, object] = {}

    def fake_find(session, prompt_type, workspace_id, datasource_id):
        captured.update(
            session=session,
            prompt_type=prompt_type,
            workspace_id=workspace_id,
            datasource_id=datasource_id,
        )
        return "adapter prompt", [{"id": 2}]

    session = cast(Session, object())
    monkeypatch.setattr(adapter_module, "find_custom_prompts", fake_find)
    provider = XPackGenerationCustomPromptClient(session)

    result = provider.find(
        GenerationCustomPromptQuery(
            prompt_type=GenerationCustomPromptType.PREDICT_DATA,
            workspace_id=10,
            datasource_id=20,
        )
    )

    assert captured == {
        "session": session,
        "prompt_type": CustomPromptTypeEnum.PREDICT_DATA,
        "workspace_id": 10,
        "datasource_id": 20,
    }
    assert result.prompt == "adapter prompt"
    assert result.items == [{"id": 2}]


def test_xpack_adapter_uses_license_as_availability(monkeypatch):
    monkeypatch.setattr(
        adapter_module.SQLBotLicenseUtil,
        "valid",
        staticmethod(lambda: False),
    )

    provider = XPackGenerationCustomPromptClient(cast(Session, object()))

    assert provider.is_enabled() is False
