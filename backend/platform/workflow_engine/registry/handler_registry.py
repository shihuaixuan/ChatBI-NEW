from typing import Any


class HandlerRegistry:
    """保存节点处理器白名单。

    流程定义只能引用这里已注册的名称，避免定义文件动态导入或执行任意代码。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        if not name:
            raise ValueError("HANDLER_NAME_REQUIRED")
        if name in self._handlers:
            raise ValueError(f"HANDLER_ALREADY_REGISTERED: {name}")
        self._handlers[name] = handler

    def contains(self, name: str) -> bool:
        return name in self._handlers

    def get(self, name: str) -> Any:
        try:
            return self._handlers[name]
        except KeyError:
            raise KeyError(f"HANDLER_NOT_FOUND: {name}") from None
