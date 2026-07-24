"""SSE 协议与助手外部 Schema 适配的契约测试。"""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import orjson

from apps.ai_model import runtime as model_runtime_module
from apps.ai_model.runtime import build_llm_runtime
from apps.chatbi.adapters.assistant_schema import (
    is_dynamic_assistant,
    load_assistant_schema,
)
from apps.chatbi.api.sse import build_role_prompt_log, encode_sse_event


@dataclass(frozen=True)
class RoleMessage:
    role: str
    content: str


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

    assert build_role_prompt_log(messages, assistant_content="answer") == [
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


def test_is_dynamic_assistant_only_for_external_types():
    assert is_dynamic_assistant(SimpleNamespace(type=1)) is True
    assert is_dynamic_assistant(SimpleNamespace(type=3)) is True
    assert is_dynamic_assistant(SimpleNamespace(type=0)) is False
    assert is_dynamic_assistant(None) is False


def test_load_assistant_schema_reads_external_catalog(monkeypatch):
    calls: list[tuple[object, ...]] = []

    class FakeCatalog:
        def get_db_schema(self, datasource_id, question, embedding=False, table_list=None):
            calls.append((datasource_id, question, embedding, table_list))
            return "schema text"

    monkeypatch.setattr(
        "apps.chatbi.adapters.assistant_schema.AssistantOutDsFactory.get_instance",
        lambda _assistant: FakeCatalog(),
    )

    schema = load_assistant_schema(
        SimpleNamespace(type=1),
        datasource_id=9,
        question="GMV",
        embedding=False,
        table_names=["orders"],
    )

    assert schema == "schema text"
    assert calls == [(9, "GMV", False, ["orders"])]


def test_build_llm_runtime_can_disable_reasoning(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConfig:
        model_id = 1
        model_name = "demo"
        additional_params = {"extra_body": {"enable_thinking": True}}

    class FakeLLM:
        llm = object()

    async def fake_get_default_config(model_id):
        captured["model_id"] = model_id
        return FakeConfig()

    def fake_create_llm(config):
        captured["thinking"] = config.additional_params.get("extra_body")
        return FakeLLM()

    monkeypatch.setattr(model_runtime_module, "get_default_config", fake_get_default_config)
    monkeypatch.setattr(model_runtime_module.LLMFactory, "create_llm", fake_create_llm)

    runtime = asyncio.run(build_llm_runtime(None, no_reasoning=True))
    assert runtime.llm is FakeLLM.llm
    assert captured["thinking"] == {}
