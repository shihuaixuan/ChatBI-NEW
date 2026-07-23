from types import SimpleNamespace

import pytest

from apps.chatbi.api import legacy_chat_flow as llm_module
from apps.chatbi.services.conversation.dataset_binding import (
    DatasetBindingError,
    validate_assistant_dataset_binding,
)


class _GenerationContextService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def build_term_context(
        self,
        workspace_id: int,
        dataset_id: int | None,
        question: str,
    ) -> tuple[str, list[dict[str, object]]]:
        if dataset_id is None:
            return "", []
        self.calls.append(
            (workspace_id, dataset_id, question, 10)
        )
        items: list[dict[str, object]] = [
            {
                "words": ["销售额", "GMV"],
                "description": "支付成功订单金额",
            }
        ]
        return '[{"words":["销售额","GMV"]}]', items


def test_dataset_bound_chat_uses_semantic_term_context(monkeypatch):
    context_service = _GenerationContextService()
    service = _service(dataset_id=20)
    _patch_logs(monkeypatch)
    monkeypatch.setattr(
        llm_module,
        "build_generation_context_service",
        lambda _session: context_service,
    )

    service.load_term_context(object())

    assert context_service.calls == [(1, 20, "GMV是多少", 10)]
    assert service.chat_question.terminologies == (
        '[{"words":["销售额","GMV"]}]'
    )


def test_unbound_assistant_has_no_semantic_term_context(monkeypatch):
    context_service = _GenerationContextService()
    service = _service(dataset_id=None)
    service.current_assistant = SimpleNamespace(oid=9, type=1)
    _patch_logs(monkeypatch)
    monkeypatch.setattr(
        llm_module,
        "build_generation_context_service",
        lambda _session: context_service,
    )

    service.chat_question.terminologies = "stale context"
    service.load_term_context(object())

    assert service.chat_question.terminologies == ""
    assert context_service.calls == []


def test_dynamic_datasource_assistant_rejects_local_semantic_dataset():
    with pytest.raises(DatasetBindingError, match="不能绑定本地 Semantic 数据集"):
        validate_assistant_dataset_binding(dataset_id=20, assistant_type=1)

    validate_assistant_dataset_binding(dataset_id=20, assistant_type=0)


def _service(dataset_id: int | None):
    service = object.__new__(llm_module.LLMService)
    service.chat_oid = 1
    service.current_user = SimpleNamespace(oid=1)
    service.current_assistant = None
    service.current_logs = {}
    service.record = SimpleNamespace(id=100, dataset_id=dataset_id)
    service.chat_question = SimpleNamespace(
        question="GMV是多少",
        terminologies="",
    )
    return service


def _patch_logs(monkeypatch):
    monkeypatch.setattr(llm_module, "start_log", lambda **_kwargs: "started")
    monkeypatch.setattr(
        llm_module,
        "end_log",
        lambda **kwargs: kwargs["log"],
    )
