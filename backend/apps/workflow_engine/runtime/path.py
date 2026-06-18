from typing import Any


class PathResolutionError(ValueError):
    """点路径无法解析时使用的内部错误。"""


def split_path(path: str) -> list[str]:
    """拆分并校验 Context 点路径。

    至少包含命名空间和字段两段，防止调用方把整个命名空间替换掉。
    """

    parts = path.split(".")
    if len(parts) < 2 or any(not part for part in parts):
        raise PathResolutionError(f"非法路径: {path!r}")
    return parts


def read_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in split_path(path):
        if not isinstance(current, dict) or part not in current:
            raise PathResolutionError(f"路径不存在: {path!r}")
        current = current[part]
    return current


def write_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = split_path(path)
    current: dict[str, Any] = data
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise PathResolutionError(f"路径中间节点不是对象: {path!r}")
        current = child
    current[parts[-1]] = value


def remove_path(data: dict[str, Any], path: str) -> None:
    parts = split_path(path)
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)
