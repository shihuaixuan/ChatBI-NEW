from collections.abc import Mapping
from typing import Protocol


class PlatformParameterRepository(Protocol):
    """平台参数读取仓储端口。"""

    async def list_group(self, group: str) -> Mapping[str, str | None]: ...


__all__ = ["PlatformParameterRepository"]
