from typing import Any, cast

from sqlbot_xpack.config import arg_manage  # type: ignore[import-untyped]
from sqlmodel import Session


class SQLModelPlatformParameterRepository:
    """基于现有平台参数存储的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_group(self, group: str) -> dict[str, str | None]:
        items = cast(
            list[Any],
            await arg_manage.get_group_args(session=self._session, flag=group),
        )
        return {item.pkey: item.pval for item in items}


__all__ = ["SQLModelPlatformParameterRepository"]
