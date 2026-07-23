from typing import cast

from apps.chatbi.models import (
    GenerationCustomPromptQuery,
    GenerationCustomPromptResult,
    GenerationCustomPromptType,
)
from apps.chatbi.services.generation import (
    GenerationCustomPromptClient,
    GenerationCustomPromptService,
)


class StubProvider:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.queries: list[GenerationCustomPromptQuery] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def find(
        self,
        query: GenerationCustomPromptQuery,
    ) -> GenerationCustomPromptResult:
        self.queries.append(query)
        return GenerationCustomPromptResult(
            prompt="custom prompt",
            items=[{"id": 1}],
        )


def test_enabled_custom_prompt_service_delegates_stable_query():
    provider = StubProvider(enabled=True)
    service = GenerationCustomPromptService(
        cast(GenerationCustomPromptClient, provider)
    )
    query = GenerationCustomPromptQuery(
        prompt_type=GenerationCustomPromptType.GENERATE_SQL,
        workspace_id=10,
        datasource_id=20,
    )

    result = service.query(query)

    assert service.enabled is True
    assert provider.queries == [query]
    assert result.prompt == "custom prompt"
    assert result.items == [{"id": 1}]


def test_disabled_custom_prompt_service_keeps_empty_result_without_querying():
    provider = StubProvider(enabled=False)
    service = GenerationCustomPromptService(
        cast(GenerationCustomPromptClient, provider)
    )

    result = service.query(
        GenerationCustomPromptQuery(
            prompt_type=GenerationCustomPromptType.ANALYSIS,
            workspace_id=10,
        )
    )

    assert service.enabled is False
    assert provider.queries == []
    assert result == GenerationCustomPromptResult()


def test_custom_prompt_types_keep_xpack_contract_values():
    assert [item.value for item in GenerationCustomPromptType] == [
        "GENERATE_SQL",
        "ANALYSIS",
        "PREDICT_DATA",
    ]
