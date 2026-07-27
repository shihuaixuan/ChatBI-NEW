"""OpenTelemetry GenAI 属性白名单。"""

from collections.abc import Mapping
from typing import Any

_ALLOWED_ATTRIBUTES = {
    "gen_ai.operation.name",
    "gen_ai.agent.name",
    "gen_ai.agent.result",
    "gen_ai.request.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "gen_ai.tool.name",
    "gen_ai.tool.call.result",
    "app.chat.id",
    "app.record.id",
    "app.run.id",
    "app.step.id",
}


def sanitize_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """仅保留低基数、非敏感且可被 OTEL 接受的属性。"""

    if not attributes:
        return {}
    return {
        key: value
        for key, value in attributes.items()
        if key in _ALLOWED_ATTRIBUTES and isinstance(value, str | int | float | bool)
    }


def agent_attributes(*, run_id: int, record_id: int, chat_id: int) -> dict[str, Any]:
    return {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "chatbi",
        "app.run.id": run_id,
        "app.record.id": record_id,
        "app.chat.id": chat_id,
    }


def llm_attributes(*, model: str) -> dict[str, Any]:
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
    }


def tool_attributes(*, tool_name: str, step_id: int | None) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool_name,
    }
    if step_id is not None:
        attributes["app.step.id"] = step_id
    return attributes


__all__ = [
    "agent_attributes",
    "llm_attributes",
    "sanitize_attributes",
    "tool_attributes",
]
