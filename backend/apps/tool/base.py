"""工具协议基座。

工具 = pydantic 参数 schema（喂给 bind_tools）+ execute 实现。
执行永远由宿主循环经白名单分发，模型只能提名工具与参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Generic, TypeVar

import orjson
from pydantic import BaseModel

from apps.tool.definition import ToolAnnotations, ToolDefinition
from apps.tool.result import ToolResult
from apps.tool.validation import ToolArgsValidator

ContextT = TypeVar("ContextT")
ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class ToolSideEffect(StrEnum):
    """Tool 是否会修改外部业务系统。"""

    READ = "read"
    WRITE = "write"


class ToolConcurrency(StrEnum):
    """Tool 是否允许与同一轮的其他安全调用并发。"""

    PARALLEL_SAFE = "parallel_safe"
    SERIAL = "serial"


@dataclass(frozen=True, slots=True)
class ToolExecutionPolicy:
    """与具体工具名称无关的执行属性。"""

    side_effect: ToolSideEffect = ToolSideEffect.READ
    concurrency: ToolConcurrency = ToolConcurrency.SERIAL
    timeout_seconds: float | None = None
    idempotent: bool = True
    destructive: bool = False
    supports_cancellation: bool = False


class Tool(Generic[ContextT, ArgsT, ResultT]):
    """通用 Tool 契约：输入输出均有明确模型。"""

    name: ClassVar[str]
    title: ClassVar[str | None] = None
    description: ClassVar[str]
    args_model: ClassVar[type[ArgsT]]
    result_model: ClassVar[type[ResultT]]
    execution: ClassVar[ToolExecutionPolicy] = ToolExecutionPolicy()
    args_validator: ClassVar[ToolArgsValidator | None] = None

    def prepare_args(
        self,
        ctx: ContextT,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """在参数校验和执行前，把模型参数投影为可信工具参数。"""

        return dict(args)

    def execute(
        self,
        ctx: ContextT,
        args: ArgsT,
    ) -> ToolResult[ResultT]:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def definition(cls) -> ToolDefinition:
        """生成与模型厂商无关的定义。"""

        read_only = cls.execution.side_effect == ToolSideEffect.READ
        return ToolDefinition(
            name=cls.name,
            title=cls.title,
            description=cls.description,
            input_schema=cls.args_model.model_json_schema(),
            output_schema=cls.result_model.model_json_schema(),
            annotations=ToolAnnotations(
                read_only=read_only,
                destructive=cls.execution.destructive,
                idempotent=cls.execution.idempotent,
            ),
        )


def truncate_summary(summary: str, max_chars: int) -> str:
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars] + f"\n…(截断，完整结果已存档，原文 {len(summary)} 字符)"


def json_summary(data: Any, max_chars: int) -> str:
    return truncate_summary(
        orjson.dumps(data, option=orjson.OPT_INDENT_2).decode(),
        max_chars,
    )


__all__ = [
    "Tool",
    "ToolConcurrency",
    "ToolExecutionPolicy",
    "ToolSideEffect",
    "json_summary",
    "truncate_summary",
]
