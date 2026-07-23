from typing import Any


class ConditionRegistry:
    """保存确定性路由条件白名单。"""

    def __init__(self) -> None:
        self._conditions: dict[str, Any] = {}

    def register(self, name: str, evaluator: Any) -> None:
        if not name:
            raise ValueError("CONDITION_NAME_REQUIRED")
        if name in self._conditions:
            raise ValueError(f"CONDITION_ALREADY_REGISTERED: {name}")
        self._conditions[name] = evaluator

    def contains(self, name: str) -> bool:
        return name in self._conditions

    def get(self, name: str) -> Any:
        try:
            return self._conditions[name]
        except KeyError:
            raise KeyError(f"CONDITION_NOT_FOUND: {name}") from None
