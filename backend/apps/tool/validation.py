"""Tool 参数跨字段校验协议。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ToolArgsValidator(Protocol):
    """无副作用校验器；失败时抛出 ValueError。"""

    def __call__(self, context: Any, args: BaseModel) -> None: ...


__all__ = ["ToolArgsValidator"]
