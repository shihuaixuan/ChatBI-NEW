import asyncio
from collections.abc import Mapping
from typing import cast

from sqlalchemy import create_engine
from sqlmodel import Session

from apps.platform_config.models import SysArgModel
from apps.platform_config.repository import PlatformParameterRepository
from apps.platform_config.repository.sqlmodel import (
    SQLModelPlatformParameterRepository,
)
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


def test_sqlmodel_platform_parameter_repository_persists_local_parameters():
    engine = create_engine("sqlite://")
    SysArgModel.__table__.create(engine)
    with Session(engine) as session:
        repository = SQLModelPlatformParameterRepository(session)
        repository.save_models(
            [
                SysArgModel(pkey="chat.sqlbot_name", pval="本地问数"),
                SysArgModel(pkey="appearance.web", pval="logo.png"),
            ]
        )

        chat_args = {
            item.pkey: item.pval
            for item in repository.list_models("chat")
        }
        general_keys = {
            item.pkey for item in repository.list_models()
        }

    assert chat_args["chat.sqlbot_name"] == "本地问数"
    assert chat_args["chat.context_record_count"] == "3"
    assert "appearance.web" not in general_keys
