import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import orjson
from sqlmodel import Session

from apps.chat.task import legacy_dependencies as dependency_module
from apps.chat.task.legacy_adapter import (
    build_context_prompt_log,
    build_role_prompt_log,
    build_run_error_message,
    encode_sse_event,
    finalize_legacy_run,
)
from apps.chat.task.legacy_dependencies import (
    LegacySchemaContext,
    build_legacy_model_runtime,
    check_legacy_datasource_connection,
    load_legacy_external_schema_context,
    resolve_legacy_datasource,
)
from apps.datasource import DatasourceConnection, DatasourceRecord, ExternalDatasource


@dataclass(frozen=True)
class RoleMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ContextMessage(RoleMessage):
    system_context: bool = False


class FakeDatasourceService:
    def __init__(self, datasource: DatasourceRecord) -> None:
        self.datasource = datasource

    def get(self, datasource_id: int) -> DatasourceRecord:
        assert datasource_id == self.datasource.id
        return self.datasource


class FakeConnectionService:
    def __init__(self) -> None:
        self.checked: list[int] = []

    def get_version(self, datasource_id: int) -> str:
        assert datasource_id == 20
        return "16"

    def check_connection(self, datasource_id: int) -> bool:
        self.checked.append(datasource_id)
        return True


def test_encode_sse_event_keeps_legacy_frame_shape():
    frame = encode_sse_event(
        "sql-result",
        content="select 1",
        reasoning_content="reasoning",
    )

    assert frame.startswith("data:")
    assert frame.endswith("\n\n")
    assert orjson.loads(frame.removeprefix("data:").strip()) == {
        "type": "sql-result",
        "content": "select 1",
        "reasoning_content": "reasoning",
    }


def test_build_role_prompt_log_marks_system_role_and_appends_assistant():
    messages = [
        RoleMessage(role="system", content="system prompt"),
        RoleMessage(role="human", content="question"),
    ]

    assert build_role_prompt_log(messages, "answer") == [
        {
            "type": "system",
            "sqlbot_system": True,
            "content": "system prompt",
        },
        {
            "type": "human",
            "sqlbot_system": False,
            "content": "question",
        },
        {
            "type": "ai",
            "sqlbot_system": False,
            "content": "answer",
        },
    ]


def test_build_context_prompt_log_uses_explicit_context_flag():
    messages = [
        ContextMessage(
            role="human",
            content="schema context",
            system_context=True,
        ),
        ContextMessage(role="human", content="question"),
    ]

    assert build_context_prompt_log(messages, "") == [
        {
            "type": "human",
            "sqlbot_system": True,
            "content": "schema context",
        },
        {
            "type": "human",
            "sqlbot_system": False,
            "content": "question",
        },
        {
            "type": "ai",
            "sqlbot_system": False,
            "content": "",
        },
    ]


def test_run_error_message_keeps_database_error_contract():
    assert orjson.loads(
        build_run_error_message("db_connection", "offline", "trace")
    ) == {"message": "offline", "type": "db-connection-err"}
    assert orjson.loads(
        build_run_error_message("db_execution", "failed", "trace")
    ) == {
        "message": "Execute SQL Failed",
        "traceback": "failed",
        "type": "exec-sql-err",
    }


def test_finalize_legacy_run_only_finishes_successful_run():
    calls: list[object] = []
    session = object()

    finalize_legacy_run(session, False, calls.append)
    finalize_legacy_run(session, True, calls.append)

    assert calls == [session]


def test_legacy_local_datasource_uses_public_datasource_services(monkeypatch):
    datasource = DatasourceRecord(
        id=20,
        name="orders",
        type="pg",
        type_name="PostgreSQL",
        configuration="{}",
        oid=10,
    )
    connection_service = FakeConnectionService()
    monkeypatch.setattr(
        dependency_module,
        "build_datasource_service",
        lambda _session: FakeDatasourceService(datasource),
    )
    monkeypatch.setattr(
        dependency_module,
        "build_datasource_connection_service",
        lambda _session: connection_service,
    )

    runtime = resolve_legacy_datasource(cast(Session, object()), 20, None)

    assert runtime.datasource is datasource
    assert runtime.engine == "PostgreSQL16"
    assert runtime.is_external is False
    assert check_legacy_datasource_connection(cast(Session, object()), runtime)
    assert connection_service.checked == [20]


def test_legacy_external_datasource_keeps_external_catalog(monkeypatch):
    datasource = ExternalDatasource(id=30, name="external", type="mysql")
    catalog = SimpleNamespace(get_ds=lambda datasource_id: datasource)
    connection = DatasourceConnection(id=30, type="mysql", configuration="{}")
    monkeypatch.setattr(
        dependency_module,
        "build_external_datasource_connection",
        lambda _datasource, _timeout: connection,
    )
    monkeypatch.setattr(dependency_module, "get_version", lambda _connection: "8")
    monkeypatch.setattr(
        dependency_module,
        "check_connection",
        lambda **_kwargs: True,
    )

    runtime = resolve_legacy_datasource(
        cast(Session, object()),
        30,
        SimpleNamespace(type=1),
        catalog,
    )

    assert runtime.datasource is datasource
    assert runtime.engine == "mysql8"
    assert runtime.external_catalog is catalog
    assert check_legacy_datasource_connection(cast(Session, object()), runtime)


def test_legacy_model_runtime_selects_model_and_disables_reasoning(monkeypatch):
    config = SimpleNamespace(
        additional_params={"extra_body": {"enable_thinking": True}}
    )
    calls: list[int | None] = []

    async def fake_get_default_config(model_id):
        calls.append(model_id)
        return config

    monkeypatch.setattr(
        dependency_module,
        "get_default_config",
        fake_get_default_config,
    )
    monkeypatch.setattr(
        dependency_module.LLMFactory,
        "create_llm",
        staticmethod(lambda runtime_config: SimpleNamespace(llm=runtime_config)),
    )

    runtime = asyncio.run(build_legacy_model_runtime("17", no_reasoning=True))

    assert calls == [17]
    assert runtime.config is config
    assert runtime.llm is config
    assert config.additional_params == {"extra_body": {}}


def test_legacy_external_schema_context_reads_external_catalog_only():
    catalog = SimpleNamespace(
        get_db_schema=lambda *args, **kwargs: "external schema"
    )

    result = load_legacy_external_schema_context(
        catalog,
        datasource_id=30,
        question="订单数",
        embedding=True,
        table_names=None,
    )

    assert result == LegacySchemaContext(schema="external schema")
