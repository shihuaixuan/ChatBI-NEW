import asyncio
from collections.abc import Mapping
from typing import cast

from apps.system.repository import SystemParameterRepository
from apps.system.services import SystemParameterService


class FakeSystemParameterRepository:
    async def list_group(self, group: str) -> Mapping[str, str | None]:
        assert group == "chat"
        return {"chat.limit_rows": "false"}


def test_system_parameter_service_returns_parameter_mapping():
    service = SystemParameterService(
        cast(SystemParameterRepository, FakeSystemParameterRepository())
    )

    assert asyncio.run(service.list_group("chat")) == {
        "chat.limit_rows": "false"
    }
