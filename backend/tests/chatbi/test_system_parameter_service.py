import asyncio
from collections.abc import Mapping
from typing import cast

from apps.platform_config.repository import PlatformParameterRepository
from apps.platform_config.services import PlatformParameterService


class FakePlatformParameterRepository:
    async def list_group(self, group: str) -> Mapping[str, str | None]:
        assert group == "chat"
        return {"chat.limit_rows": "false"}


def test_platform_parameter_service_returns_parameter_mapping():
    service = PlatformParameterService(
        cast(PlatformParameterRepository, FakePlatformParameterRepository())
    )

    assert asyncio.run(service.list_group("chat")) == {
        "chat.limit_rows": "false"
    }
