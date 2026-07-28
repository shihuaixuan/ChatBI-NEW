"""ToolDefinition 到 OpenAI Function Calling 的转换。"""

from __future__ import annotations

from typing import Any

from apps.tool.definition import ToolDefinition


def to_openai_tool_spec(definition: ToolDefinition) -> dict[str, Any]:
    """只转换模型协议，不参与 Tool 注册和执行。"""

    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.input_schema,
        },
    }


def to_openai_tool_specs(
    definitions: list[ToolDefinition],
) -> list[dict[str, Any]]:
    return [to_openai_tool_spec(definition) for definition in definitions]


__all__ = ["to_openai_tool_spec", "to_openai_tool_specs"]
