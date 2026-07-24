"""术语上下文与数据集绑定规则测试。"""

from types import SimpleNamespace

import pytest

from apps.chatbi.services.conversation.dataset_binding import (
    DatasetBindingError,
    validate_assistant_dataset_binding,
)
from apps.chatbi.services.generation.context.knowledge import GenerationContextService


class _TermResult:
    def __init__(self, words: list[str], description: str) -> None:
        self.words = words
        self.description = description


class _TermQueryService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None, str, int]] = []

    def search(
        self,
        workspace_id: int,
        dataset_id: int,
        question: str,
        *,
        limit: int = 10,
    ) -> list[_TermResult]:
        self.calls.append((workspace_id, dataset_id, question, limit))
        return [
            _TermResult(words=["销售额", "GMV"], description="支付成功订单金额")
        ]


def test_dataset_bound_generation_context_loads_terms():
    term_service = _TermQueryService()
    service = GenerationContextService(
        sql_example_service=SimpleNamespace(),  # type: ignore[arg-type]
        term_query_service=term_service,  # type: ignore[arg-type]
    )

    prompt, items = service.build_term_context(1, 20, "GMV是多少")

    assert term_service.calls == [(1, 20, "GMV是多少", 10)]
    assert "销售额" in prompt
    assert items == [
        {
            "words": ["销售额", "GMV"],
            "description": "支付成功订单金额",
        }
    ]


def test_unbound_generation_context_has_no_terms():
    term_service = _TermQueryService()
    service = GenerationContextService(
        sql_example_service=SimpleNamespace(),  # type: ignore[arg-type]
        term_query_service=term_service,  # type: ignore[arg-type]
    )

    prompt, items = service.build_term_context(1, None, "GMV是多少")

    assert prompt == ""
    assert items == []
    assert term_service.calls == []


def test_dynamic_datasource_assistant_rejects_local_semantic_dataset():
    with pytest.raises(DatasetBindingError, match="外部动态数据源助手不能绑定本地 Semantic 数据集"):
        validate_assistant_dataset_binding(dataset_id=20, assistant_type=1)

    validate_assistant_dataset_binding(dataset_id=20, assistant_type=0)
