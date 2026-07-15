"""Agentic 工具基座。

工具 = pydantic 参数 schema（喂给 bind_tools 供 LLM 选择）+ execute 实现。
执行永远由 AgentLoop 经白名单分发，LLM 只能提名工具与参数。

返回统一为 ToolOutput：
- summary：回写进 LLM 消息历史（ToolMessage），受字数上限约束；
- payload：完整结构化结果，落库/给前端，不进上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Type

import orjson
from pydantic import BaseModel


@dataclass
class AgentToolContext:
    """一次 run 的执行上下文（由 API 层组装，工具只读）。"""

    session: Any
    oid: int
    user_id: int | None
    datasource_id: int | None
    dataset_id: int | None = None
    config: Any = None
    # 循环内跨工具共享的运行时状态（语义包、执行结果标记等），由 loop 维护。
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutput:
    success: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None


class AgentTool:
    """工具基类：子类声明 name/description/args_model 并实现 execute。"""

    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[Type[BaseModel]]

    def execute(self, ctx: AgentToolContext, args: BaseModel) -> ToolOutput:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def tool_spec(cls) -> dict[str, Any]:
        """OpenAI function-calling 形态的工具定义，供 bind_tools 使用。"""

        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.args_model.model_json_schema(),
            },
        }


def truncate_summary(summary: str, max_chars: int) -> str:
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars] + f"\n…(截断，完整结果已存档，原文 {len(summary)} 字符)"


def json_summary(data: Any, max_chars: int) -> str:
    return truncate_summary(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode(), max_chars)
