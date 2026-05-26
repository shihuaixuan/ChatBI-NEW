import time
from types import SimpleNamespace

import pytest

from apps.chat.models.chat_model import ChatQuestion, OperationEnum
from apps.chat.task import llm as llm_module
from apps.chat.task.llm import LLMService
from apps.semantic.models.semantic_model import AssetType
from apps.semantic.models.semantic_schema import (
    SemanticAssetMatch,
    SemanticRetrieveResponse,
)


def _service() -> LLMService:
    service = object.__new__(LLMService)
    service.current_logs = {}
    service.record = SimpleNamespace(id=101)
    service.chat_question = ChatQuestion(chat_id=1, question="统计销售额", datasource_id=9)
    return service


def _set_semantic_settings(
    monkeypatch,
    enabled: bool,
    timeout_ms: int = 800,
    approved_only: bool = True,
    metric_top_k: int = 5,
    dimension_top_k: int = 8,
) -> None:
    fields = getattr(llm_module.settings.__class__, "model_fields", {})
    if "SEMANTIC_LAYER_ENABLED" in fields:
        monkeypatch.setattr(llm_module.settings, "SEMANTIC_LAYER_ENABLED", enabled)
        monkeypatch.setattr(llm_module.settings, "SEMANTIC_SEARCH_TIMEOUT_MS", timeout_ms)
        monkeypatch.setattr(llm_module.settings, "SEMANTIC_APPROVED_ONLY", approved_only)
        monkeypatch.setattr(llm_module.settings, "SEMANTIC_METRIC_TOP_K", metric_top_k)
        monkeypatch.setattr(llm_module.settings, "SEMANTIC_DIMENSION_TOP_K", dimension_top_k)
    else:
        monkeypatch.setattr(llm_module.settings.__class__, "SEMANTIC_LAYER_ENABLED", enabled, raising=False)
        monkeypatch.setattr(llm_module.settings.__class__, "SEMANTIC_SEARCH_TIMEOUT_MS", timeout_ms, raising=False)
        monkeypatch.setattr(llm_module.settings.__class__, "SEMANTIC_APPROVED_ONLY", approved_only, raising=False)
        monkeypatch.setattr(llm_module.settings.__class__, "SEMANTIC_METRIC_TOP_K", metric_top_k, raising=False)
        monkeypatch.setattr(llm_module.settings.__class__, "SEMANTIC_DIMENSION_TOP_K", dimension_top_k, raising=False)


@pytest.fixture
def capture_chat_logs(monkeypatch):
    captured = {"started": [], "ended": []}

    def fake_start_log(**kwargs):
        log = SimpleNamespace(id=1, operate=kwargs["operate"], messages=None)
        captured["started"].append(kwargs)
        return log

    def fake_end_log(**kwargs):
        kwargs["log"].messages = kwargs["full_message"]
        captured["ended"].append(kwargs)
        return kwargs["log"]

    monkeypatch.setattr(llm_module, "start_log", fake_start_log)
    monkeypatch.setattr(llm_module, "end_log", fake_end_log)
    return captured


def test_filter_semantic_assets_skips_retrieval_when_disabled(monkeypatch, capture_chat_logs):
    service = _service()
    _set_semantic_settings(monkeypatch, enabled=False)

    def fail_retrieve(*_args, **_kwargs):
        raise AssertionError("semantic retrieval should not run when disabled")

    monkeypatch.setattr(llm_module, "retrieve_semantic_assets", fail_retrieve, raising=False)

    service.filter_semantic_assets(SimpleNamespace(), oid=7, ds_id=9)

    assert service.chat_question.semantic_context == ""
    assert capture_chat_logs["started"] == []


def test_filter_semantic_assets_injects_context_and_logs_matches(monkeypatch, capture_chat_logs):
    service = _service()
    _set_semantic_settings(monkeypatch, enabled=True, approved_only=False, metric_top_k=2, dimension_top_k=3)
    seen_request = {}

    def fake_retrieve(_session, request, embedding_model=None):
        seen_request["request"] = request
        seen_request["embedding_model"] = embedding_model
        return SemanticRetrieveResponse(
            metrics=[
                SemanticAssetMatch(
                    asset_type=AssetType.METRIC.value,
                    asset_id=3,
                    name="sales_amount",
                    display_name="销售额",
                    score=0.95,
                    match_word="销售额",
                    snapshot={"expr": "pay_amount", "default_agg": "SUM"},
                )
            ],
            dimensions=[],
            degraded=True,
            reason="embedding model unavailable",
        )

    monkeypatch.setattr(llm_module, "retrieve_semantic_assets", fake_retrieve, raising=False)
    monkeypatch.setattr(llm_module, "build_prompt_context", lambda _response: "已审核业务指标:\n- 销售额")
    saved_usage = {}

    def fake_save_before_sql(session, record_id, matches, semantic_context):
        saved_usage["session"] = session
        saved_usage["record_id"] = record_id
        saved_usage["matches"] = matches
        saved_usage["semantic_context"] = semantic_context

    monkeypatch.setattr(llm_module, "save_matched_and_injected_semantic_assets", fake_save_before_sql, raising=False)

    session = SimpleNamespace()
    service.filter_semantic_assets(session, oid=7, ds_id=9)

    assert seen_request["request"].oid == 7
    assert seen_request["request"].datasource_id == 9
    assert seen_request["request"].question == "统计销售额"
    assert seen_request["request"].approved_only is False
    assert seen_request["request"].metric_top_k == 2
    assert seen_request["request"].dimension_top_k == 3
    assert seen_request["embedding_model"] is None
    assert service.chat_question.semantic_context == "已审核业务指标:\n- 销售额"
    assert capture_chat_logs["started"][0]["operate"] == OperationEnum.FILTER_SEMANTIC_ASSET
    assert capture_chat_logs["started"][0]["record_id"] == 101
    logged = capture_chat_logs["ended"][0]["full_message"]
    assert logged["question"] == "统计销售额"
    assert logged["datasource_id"] == 9
    assert logged["semantic_context"] == "已审核业务指标:\n- 销售额"
    assert logged["degraded"] is True
    assert logged["degradation_reason"] == "embedding model unavailable"
    assert logged["hit_count"] == 1
    assert logged["top_score"] == 0.95
    assert logged["metrics"][0]["asset_id"] == 3
    assert logged["metrics"][0]["score"] == 0.95
    assert saved_usage["session"] is session
    assert saved_usage["record_id"] == 101
    assert saved_usage["matches"][0].asset_id == 3
    assert saved_usage["semantic_context"] == "已审核业务指标:\n- 销售额"


def test_filter_semantic_assets_degrades_on_retrieval_exception(monkeypatch, capture_chat_logs):
    service = _service()
    _set_semantic_settings(monkeypatch, enabled=True)

    def fail_retrieve(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_module, "retrieve_semantic_assets", fail_retrieve, raising=False)

    service.filter_semantic_assets(SimpleNamespace(), oid=7, ds_id=9)

    assert service.chat_question.semantic_context == ""
    logged = capture_chat_logs["ended"][0]["full_message"]
    assert logged["degraded"] is True
    assert logged["degradation_reason"] == "boom"
    assert logged["semantic_context"] == ""


def test_filter_semantic_assets_degrades_on_timeout(monkeypatch, capture_chat_logs):
    service = _service()
    _set_semantic_settings(monkeypatch, enabled=True, timeout_ms=1)

    def slow_retrieve(*_args, **_kwargs):
        time.sleep(0.05)
        return SemanticRetrieveResponse()

    monkeypatch.setattr(llm_module, "retrieve_semantic_assets", slow_retrieve, raising=False)

    started_at = time.monotonic()
    service.filter_semantic_assets(SimpleNamespace(), oid=7, ds_id=9)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.04
    assert service.chat_question.semantic_context == ""
    logged = capture_chat_logs["ended"][0]["full_message"]
    assert logged["degraded"] is True
    assert logged["degradation_reason"] == "semantic search timeout"


def test_filter_semantic_assets_ignores_usage_save_failure(monkeypatch, capture_chat_logs):
    service = _service()
    _set_semantic_settings(monkeypatch, enabled=True)

    monkeypatch.setattr(
        llm_module,
        "retrieve_semantic_assets",
        lambda *_args, **_kwargs: SemanticRetrieveResponse(
            metrics=[
                SemanticAssetMatch(
                    asset_type=AssetType.METRIC.value,
                    asset_id=3,
                    name="sales_amount",
                    display_name="销售额",
                    score=0.95,
                    snapshot={"expr": "pay_amount"},
                )
            ],
            dimensions=[],
        ),
        raising=False,
    )
    monkeypatch.setattr(llm_module, "build_prompt_context", lambda _response: "已审核业务指标")

    def fail_save(*_args, **_kwargs):
        raise RuntimeError("usage store unavailable")

    monkeypatch.setattr(llm_module, "save_matched_and_injected_semantic_assets", fail_save, raising=False)

    service.filter_semantic_assets(SimpleNamespace(rollback=lambda: None), oid=7, ds_id=9)

    assert service.chat_question.semantic_context == "已审核业务指标"
    assert capture_chat_logs["ended"][0]["full_message"]["semantic_context"] == "已审核业务指标"


def test_check_save_sql_saves_used_semantic_assets_before_sql_record(monkeypatch):
    service = _service()
    service.current_logs = {OperationEnum.GENERATE_SQL: SimpleNamespace(id=1)}
    service.semantic_asset_matches = [
        SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=3,
            name="sales_amount",
            display_name="销售额",
            score=0.95,
            snapshot={"expr": "pay_amount"},
        )
    ]
    calls = []

    monkeypatch.setattr(LLMService, "check_sql", lambda *_args, **_kwargs: ("SELECT SUM(pay_amount) FROM orders", []))

    def fake_save_used(session, record_id, matches, sql):
        calls.append(("usage", session, record_id, matches, sql))

    def fake_save_sql(session, sql, record_id):
        calls.append(("sql", session, record_id, sql))

    monkeypatch.setattr(llm_module, "save_used_semantic_assets", fake_save_used, raising=False)
    monkeypatch.setattr(llm_module, "save_sql", fake_save_sql)

    session = SimpleNamespace()
    sql = service.check_save_sql(session, res="{}", operate=OperationEnum.GENERATE_SQL)

    assert sql == "SELECT SUM(pay_amount) FROM orders"
    assert calls[0][0] == "usage"
    assert calls[0][2] == 101
    assert calls[0][3][0].asset_id == 3
    assert calls[0][4] == "SELECT SUM(pay_amount) FROM orders"
    assert calls[1] == ("sql", session, 101, "SELECT SUM(pay_amount) FROM orders")


def test_check_save_sql_ignores_used_usage_save_failure(monkeypatch):
    service = _service()
    service.current_logs = {OperationEnum.GENERATE_SQL: SimpleNamespace(id=1)}
    service.semantic_asset_matches = [
        SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=3,
            name="sales_amount",
            display_name="销售额",
            score=0.95,
            snapshot={"expr": "pay_amount"},
        )
    ]
    saved_sql = {}
    session = SimpleNamespace(rollback=lambda: saved_sql.setdefault("rolled_back", True))

    monkeypatch.setattr(LLMService, "check_sql", lambda *_args, **_kwargs: ("SELECT SUM(pay_amount) FROM orders", []))
    monkeypatch.setattr(
        llm_module,
        "save_used_semantic_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("usage store unavailable")),
        raising=False,
    )
    monkeypatch.setattr(
        llm_module,
        "save_sql",
        lambda session, sql, record_id: saved_sql.update({"sql": sql, "record_id": record_id}),
    )

    sql = service.check_save_sql(session, res="{}", operate=OperationEnum.GENERATE_SQL)

    assert sql == "SELECT SUM(pay_amount) FROM orders"
    assert saved_sql == {
        "rolled_back": True,
        "sql": "SELECT SUM(pay_amount) FROM orders",
        "record_id": 101,
    }
