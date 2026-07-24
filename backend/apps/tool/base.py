"""工具协议基座。

工具 = pydantic 参数 schema（喂给 bind_tools）+ execute 实现。
执行永远由宿主循环经白名单分发，模型只能提名工具与参数。
"""

from __future__ import annotations

from typing import Any, ClassVar

import orjson
from pydantic import BaseModel

from apps.tool.output import ToolOutput


class Tool:
    """通用工具基类：子类声明 name/description/args_model 并实现 execute。"""

    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    is_read_only: ClassVar[bool] = True
    is_concurrency_safe: ClassVar[bool] = False

    def execute(self, ctx: Any, args: BaseModel) -> ToolOutput:  # pragma: no cover - interface
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
    return truncate_summary(
        orjson.dumps(data, option=orjson.OPT_INDENT_2).decode(),
        max_chars,
    )
