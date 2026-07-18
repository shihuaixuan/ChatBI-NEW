import json
from types import SimpleNamespace

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
    monkeypatch.setattr(
        llm_module,
        "get_terminology_template",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("绑定数据集时不得读取旧术语表")
        ),
    )

    service.filter_terminology_template(object(), oid=1, ds_id=30)

    assert query.calls == [(1, 20, "GMV是多少", 10)]
    assert json.loads(service.chat_question.terminologies) == [
        {
            "words": ["销售额", "GMV"],
            "description": "支付成功订单金额",
        }
    ]


def test_unbound_assistant_uses_explicit_legacy_compatibility(monkeypatch):
    calls = []
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

    def legacy_query(session, question, oid, datasource_id):
        calls.append((session, question, oid, datasource_id))
        return "legacy", [{"words": ["旧术语"]}]

    monkeypatch.setattr(llm_module, "get_terminology_template", legacy_query)
    session = object()

    service.filter_terminology_template(session, oid=1, ds_id=30)

    assert calls == [(session, "GMV是多少", 9, None)]
    assert service.chat_question.terminologies == "legacy"


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
