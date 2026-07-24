from types import SimpleNamespace

from apps.chatbi.api import legacy_chat_flow as llm_module
from apps.conversation.models import ChatFinishStep
from common.error import SingleMessageError


class FakeSessionMaker:
    def __init__(self) -> None:
        self.session = object()
        self.remove_calls = 0

    def __call__(self):
        return self.session

    def remove(self) -> None:
        self.remove_calls += 1


def test_legacy_chat_run_does_not_overwrite_failed_record(monkeypatch):
    session_maker = FakeSessionMaker()
    monkeypatch.setattr(llm_module, "session_maker", session_maker)

    service = object.__new__(llm_module.LLMService)
    service.ds = None
    service.record = SimpleNamespace(id=7)
    failure_calls: list[str] = []
    success_calls: list[object] = []

    def fail_datasource_selection(_session):
        raise SingleMessageError("datasource selection failed")

    service.select_datasource = fail_datasource_selection
    service.save_error = lambda session, message: failure_calls.append(message)
    service.finish = lambda session: success_calls.append(session)

    result = list(service.run_task(in_chat=False, stream=False))

    assert result == [
        {
            "success": False,
            "record_id": 7,
            "message": "datasource selection failed",
        }
    ]
    assert failure_calls == ["datasource selection failed"]
    assert success_calls == []
    assert session_maker.remove_calls == 1


def test_legacy_chat_run_preserves_successful_finalization(monkeypatch):
    session_maker = FakeSessionMaker()
    monkeypatch.setattr(llm_module, "session_maker", session_maker)
    monkeypatch.setattr(
        llm_module,
        "check_legacy_datasource_connection",
        lambda *_args: True,
    )
    monkeypatch.setattr(llm_module, "requires_data_policy", lambda user: False)

    service = object.__new__(llm_module.LLMService)
    service.ds = SimpleNamespace(type="postgres")
    service.connection = object()
    service.datasource_runtime = object()
    service.record = SimpleNamespace(id=7)
    service.chat_question = SimpleNamespace(question="查询订单", sql=None)
    service.current_user = object()
    service.current_assistant = None
    service.change_title = False
    service._save_record_sql = lambda _session, _sql: None
    success_calls: list[object] = []

    service.load_term_context = lambda session: None
    service.filter_training_template = lambda session, oid, ds_id: None
    service.init_messages = lambda session: None
    service.validate_history_ds = lambda session: None
    service.generate_sql = lambda session: iter(
        [
            SimpleNamespace(
                kind="result",
                error=None,
                result=SimpleNamespace(
                    chart_type="table",
                    brief="",
                    sql="select 1",
                    tables=[],
                ),
            )
        ]
    )
    service.finish = lambda session: success_calls.append(session)

    result = list(
        service.run_task(
            in_chat=False,
            stream=False,
            finish_step=ChatFinishStep.GENERATE_SQL,
        )
    )

    assert result == [{"success": True, "record_id": 7, "sql": "select 1"}]
    assert success_calls == [session_maker.session]
    assert session_maker.remove_calls == 1


def test_legacy_chat_run_sse_event_sequence_until_sql_finish(monkeypatch):
    """R3-b 特征测试：锁定 in_chat 模式到 GENERATE_SQL 提前结束的完整 SSE 事件序列。"""

    session_maker = FakeSessionMaker()
    monkeypatch.setattr(llm_module, "session_maker", session_maker)
    monkeypatch.setattr(
        llm_module,
        "check_legacy_datasource_connection",
        lambda *_args: True,
    )
    monkeypatch.setattr(llm_module, "requires_data_policy", lambda user: False)

    service = object.__new__(llm_module.LLMService)
    service.ds = SimpleNamespace(type="postgres")
    service.connection = object()
    service.datasource_runtime = object()
    service.record = SimpleNamespace(
        id=7, regenerate_record_id=None, question="查询订单"
    )
    service.chat_question = SimpleNamespace(question="查询订单", sql=None)
    service.current_user = object()
    service.current_assistant = None
    service.change_title = False
    service._save_record_sql = lambda _session, _sql: None
    service.current_logs = {}

    service.load_term_context = lambda session: None
    service.filter_training_template = lambda session, oid, ds_id: None
    service.init_messages = lambda session: None
    service.validate_history_ds = lambda session: None
    service.generate_sql = lambda session: iter(
        [
            SimpleNamespace(
                kind="chunk", content="{\"sql\":", reasoning_content="思考"
            ),
            SimpleNamespace(
                kind="result",
                error=None,
                result=SimpleNamespace(
                    chart_type="table", brief="", sql="select 1", tables=[]
                ),
            ),
        ]
    )
    service.finish = lambda session: None

    frames = list(
        service.run_task(
            in_chat=True,
            stream=True,
            finish_step=ChatFinishStep.GENERATE_SQL,
        )
    )

    import sqlparse

    from apps.chatbi.api.legacy_sse import encode_sse_event

    assert frames == [
        encode_sse_event("id", id=7),
        encode_sse_event("question", question="查询订单"),
        encode_sse_event(
            "sql-result", content="{\"sql\":", reasoning_content="思考"
        ),
        encode_sse_event("info", msg="sql generated"),
        encode_sse_event(
            "sql", content=sqlparse.format("select 1", reindent=True)
        ),
        encode_sse_event("finish"),
    ]
