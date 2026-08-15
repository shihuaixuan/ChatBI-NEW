"""用户记忆领域错误。"""


class MemoryError(Exception):
    """用户记忆模块错误基类。"""


class MemoryNotFoundError(MemoryError, ValueError):
    """用户记忆不存在或不属于当前用户。"""

    def __init__(self, memory_id: int) -> None:
        super().__init__(f"MEMORY_NOT_FOUND:{memory_id}")


class MemoryUsageNotFoundError(MemoryError, ValueError):
    """记忆使用记录不存在或不属于当前用户。"""

    def __init__(self, usage_id: int) -> None:
        super().__init__(f"MEMORY_USAGE_NOT_FOUND:{usage_id}")


class MemoryValidationError(MemoryError, ValueError):
    """用户记忆输入不满足模块约束。"""


class MemoryConflictError(MemoryError, ValueError):
    """用户记忆与已有有效记忆冲突。"""
