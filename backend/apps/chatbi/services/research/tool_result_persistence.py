"""Research ToolResult 的持久化序列化和恢复。"""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from apps.chatbi.models.dto.research_agent import (
    RESEARCH_TOOL_RESULT_SCHEMA_VERSION,
    ToolResult,
)

ResultT = TypeVar("ResultT", bound=BaseModel)


def serialize_research_tool_result(result: ToolResult[ResultT]) -> dict[str, Any]:
    """将 ToolResult 封装为 Agent Tool Call 的 JSONB payload。"""

    if result.schema_version != RESEARCH_TOOL_RESULT_SCHEMA_VERSION:
        raise ValueError("RESEARCH_TOOL_RESULT_SCHEMA_VERSION_UNSUPPORTED")
    return {
        "schema_version": RESEARCH_TOOL_RESULT_SCHEMA_VERSION,
        "tool_result": result.model_dump(mode="json"),
    }


def restore_research_tool_result(
    payload: dict[str, Any],
    result_model: type[ResultT],
) -> ToolResult[ResultT]:
    """从 Tool Call 的 JSONB payload 恢复带类型结果的 ToolResult。"""

    if payload.get("schema_version") != RESEARCH_TOOL_RESULT_SCHEMA_VERSION:
        raise ValueError("RESEARCH_TOOL_RESULT_SCHEMA_VERSION_UNSUPPORTED")
    raw_result = payload.get("tool_result")
    if not isinstance(raw_result, dict):
        raise ValueError("RESEARCH_TOOL_RESULT_PAYLOAD_INVALID")
    try:
        # 结果模型由注册工具在运行时提供，不能在静态类型参数中直接使用变量。
        result_type: Any = ToolResult.__class_getitem__(result_model)
        return cast(ToolResult[ResultT], result_type.model_validate(raw_result))
    except (TypeError, ValidationError) as exc:
        raise ValueError("RESEARCH_TOOL_RESULT_PAYLOAD_INVALID") from exc


__all__ = [
    "restore_research_tool_result",
    "serialize_research_tool_result",
]
