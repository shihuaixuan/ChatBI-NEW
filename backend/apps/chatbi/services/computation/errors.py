"""ComputeEngine 的稳定错误类型。"""

from __future__ import annotations


class ComputeEngineError(RuntimeError):
    """计算阶段无法安全执行。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class ComputeOperationError(ComputeEngineError):
    """操作名称或操作参数不符合白名单契约。"""


class ComputeExpressionError(ComputeEngineError):
    """派生表达式未通过受限语法校验。"""


__all__ = [
    "ComputeEngineError",
    "ComputeExpressionError",
    "ComputeOperationError",
]
