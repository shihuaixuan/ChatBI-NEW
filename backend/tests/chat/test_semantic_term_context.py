import json
from types import SimpleNamespace

import pytest

from apps.chat.services.semantic_binding import (
    DatasetBindingError,
    validate_assistant_dataset_binding,
)
from apps.chat.task import llm as llm_module
from apps.semantic.models.dto import TermSearchResult


class _TermQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[TermSearchResult]:
        self.calls.append((oid, dataset_id, query, limit))
        return [
            TermSearchResult(
                term_id=7,
                dataset_id=dataset_id,
                words=["销售额", "GMV"],
                description="支付成功订单金额",
            )
        ]


def test_dataset_bound_chat_uses_semantic_term_context(monkeypatch):
    query = _TermQuery()
    service = _service(dataset_id=20)
    _patch_logs(monkeypatch)
    monkeypatch.setattr(
        llm_module,
        "build_semantic_term_query_service",
        lambda _session: query,
    )

    service.load_term_context(object())

    assert query.calls == [(1, 20, "GMV是多少", 10)]
    assert json.loads(service.chat_question.terminologies) == [
        {
            "words": ["销售额", "GMV"],
            "description": "支付成功订单金额",
        }
    ]


def test_unbound_assistant_has_no_semantic_term_context(monkeypatch):
    service = _service(dataset_id=None)
    service.current_assistant = SimpleNamespace(oid=9, type=1)
    _patch_logs(monkeypatch)
    monkeypatch.setattr(
        llm_module,
        "build_semantic_term_query_service",
        lambda _session: (_ for _ in ()).throw(
            AssertionError("未绑定数据集时不能调用 Semantic 查询")
        ),
    )

    service.chat_question.terminologies = "stale context"
    service.load_term_context(object())

    assert service.chat_question.terminologies == ""


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
