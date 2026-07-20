from apps.system.repository import SystemParameterRepository


class SystemParameterService:
    """系统运行参数的公开查询入口。"""

    def __init__(self, repository: SystemParameterRepository) -> None:
        self._repository = repository

    async def list_group(self, group: str) -> dict[str, str | None]:
        return dict(await self._repository.list_group(group))


__all__ = ["SystemParameterService"]
