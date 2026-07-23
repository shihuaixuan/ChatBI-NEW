from apps.platform_config.repository import PlatformParameterRepository


class PlatformParameterService:
    """平台运行参数的公开查询入口。"""

    def __init__(self, repository: PlatformParameterRepository) -> None:
        self._repository = repository

    async def list_group(self, group: str) -> dict[str, str | None]:
        return dict(await self._repository.list_group(group))


__all__ = ["PlatformParameterService"]
