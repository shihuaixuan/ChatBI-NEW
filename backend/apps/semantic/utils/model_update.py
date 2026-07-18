from typing import Any


def assign_values(target: object, values: dict[str, Any]) -> None:
    """将已校验 DTO 中的字段值写入目标对象。"""

    for key, value in values.items():
        setattr(target, key, value)
