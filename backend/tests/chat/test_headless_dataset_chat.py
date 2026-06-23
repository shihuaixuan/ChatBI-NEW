import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.datasource.models.datasource import CoreDatasource
from apps.headless.models import HeadlessDataSet, HeadlessModel


class FakeSession:
    def __init__(self, *objects):
        self.objects = {(type(obj), obj.id): obj for obj in objects}
        self.added = []
        self.committed = False

    def get(self, model, object_id):
        return self.objects.get((model, object_id))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 900 + len(self.added)

    def refresh(self, obj):
        return None

    def commit(self):
        self.committed = True


def make_user(oid=1, user_id=10):
    return SimpleNamespace(oid=oid, id=user_id)


def make_dataset(dataset_id=20, oid=1, default_model_id=30):
    return HeadlessDataSet(
        id=dataset_id,
        oid=oid,
        domain_id=2,
        name="销售数据集",
        biz_name="sales_dataset",
        status=1,
        default_model_id=default_model_id,
    )


def make_model(model_id=30, oid=1, datasource_id=40):
    return HeadlessModel(
        id=model_id,
        oid=oid,
        domain_id=2,
        datasource_id=datasource_id,
        name="销售模型",
        biz_name="sales_model",
        status=1,
    )


def make_datasource(datasource_id=40, oid=1):
    return CoreDatasource(
        id=datasource_id,
        oid=oid,
        name="销售库",
        type="mysql",
        type_name="MySQL",
        configuration="{}",
        create_by=10,
        recommended_config=1,
    )


def import_chat_crud(monkeypatch):
    datasource_module = ModuleType("apps.datasource.crud.datasource")
    datasource_module.get_ds = lambda *args, **kwargs: None
    recommended_module = ModuleType("apps.datasource.crud.recommended_problem")
    recommended_module.get_datasource_recommended_chart = lambda *args, **kwargs: []
    db_module = ModuleType("apps.db.db")
    db_module.exec_sql = lambda *args, **kwargs: None
    assistant_module = ModuleType("apps.system.crud.assistant")
    assistant_module.AssistantOutDsFactory = type(
        "AssistantOutDsFactory",
        (),
        {"get_instance": staticmethod(lambda *args, **kwargs: None)},
    )
    monkeypatch.setitem(sys.modules, "apps.datasource.crud.datasource", datasource_module)
    monkeypatch.setitem(sys.modules, "apps.datasource.crud.recommended_problem", recommended_module)
    monkeypatch.setitem(sys.modules, "apps.db.db", db_module)
    monkeypatch.setitem(sys.modules, "apps.system.crud.assistant", assistant_module)
    sys.modules.pop("apps.chat.curd.chat", None)
    return importlib.import_module("apps.chat.curd.chat")


def test_resolve_dataset_chat_binding_returns_dataset_and_datasource():
    from apps.chat.services.headless_binding import resolve_dataset_chat_binding

    dataset = make_dataset()
    model = make_model()
    datasource = make_datasource()
    session = FakeSession(dataset, model, datasource)

    binding = resolve_dataset_chat_binding(session, make_user(), 20)

    assert binding.dataset_id == 20
    assert binding.dataset_name == "销售数据集"
    assert binding.datasource_id == 40
    assert binding.datasource_name == "销售库"
    assert binding.datasource_type == "mysql"
    assert binding.datasource_type_name == "MySQL"


def test_resolve_dataset_chat_binding_rejects_cross_workspace_dataset():
    from apps.chat.services.headless_binding import DatasetBindingError, resolve_dataset_chat_binding

    session = FakeSession(make_dataset(oid=2))

    with pytest.raises(DatasetBindingError, match="数据集不存在或无权限访问"):
        resolve_dataset_chat_binding(session, make_user(oid=1), 20)


def test_apply_dataset_binding_to_chat_and_record():
    from apps.chat.services.headless_binding import (
        DatasetChatBinding,
        apply_binding_to_chat,
        apply_binding_to_record,
    )

    binding = DatasetChatBinding(
        dataset_id=20,
        dataset_name="销售数据集",
        datasource_id=40,
        datasource_name="销售库",
        datasource_type="mysql",
        datasource_type_name="MySQL",
    )
    chat = Chat()
    record = ChatRecord()

    apply_binding_to_chat(chat, binding)
    apply_binding_to_record(record, binding)

    assert chat.dataset_id == 20
    assert chat.datasource == 40
    assert chat.engine_type == "MySQL"
    assert record.dataset_id == 20
    assert record.datasource == 40
    assert record.engine_type == "MySQL"


def test_save_question_copies_dataset_and_datasource_from_chat(monkeypatch):
    chat_crud = import_chat_crud(monkeypatch)
    from apps.chat.models.chat_model import ChatQuestion

    chat = Chat(id=77, create_by=10, oid=1, dataset_id=20, datasource=40, engine_type="MySQL")
    session = FakeSession(chat)

    record = chat_crud.save_question(session, make_user(), ChatQuestion(chat_id=77, question="销售额是多少"))

    assert record.dataset_id == 20
    assert record.datasource == 40
    assert record.engine_type == "MySQL"
    assert session.committed is True
