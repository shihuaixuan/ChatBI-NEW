from types import SimpleNamespace

from apps.chat.task import llm as llm_module
from apps.chatbi.models import ChatFinishStep
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
    monkeypatch.setattr(llm_module, "check_connection", lambda **kwargs: True)
    monkeypatch.setattr(llm_module, "requires_data_policy", lambda user: False)
    monkeypatch.setattr(llm_module, "save_sql", lambda **kwargs: None)

    service = object.__new__(llm_module.LLMService)
    service.ds = SimpleNamespace(type="postgres")
    service.connection = object()
    service.record = SimpleNamespace(id=7)
    service.chat_question = SimpleNamespace(question="查询订单", sql=None)
    service.current_user = object()
    service.current_assistant = None
    service.change_title = False
    success_calls: list[object] = []

    service.load_term_context = lambda session: None
    service.filter_training_template = lambda session, oid, ds_id: None
    service.filter_custom_prompts = lambda session, prompt_type, oid, ds_id: None
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
