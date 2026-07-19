from dataclasses import dataclass

import orjson

from apps.chat.task.legacy_adapter import (
    build_context_prompt_log,
    build_role_prompt_log,
    encode_sse_event,
)


@dataclass(frozen=True)
class RoleMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ContextMessage(RoleMessage):
    system_context: bool = False


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
