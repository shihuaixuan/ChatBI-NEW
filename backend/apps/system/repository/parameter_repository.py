from collections.abc import Mapping
from typing import Protocol


class SystemParameterRepository(Protocol):
    """系统参数读取仓储端口。"""

    async def list_group(self, group: str) -> Mapping[str, str | None]: ...


__all__ = ["SystemParameterRepository"]
