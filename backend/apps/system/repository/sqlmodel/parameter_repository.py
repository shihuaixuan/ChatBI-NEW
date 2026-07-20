from sqlbot_xpack.config.arg_manage import get_group_args  # type: ignore[import-untyped]
from sqlmodel import Session


class SQLModelSystemParameterRepository:
    """基于现有系统参数存储的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def list_group(self, group: str) -> dict[str, str | None]:
        items = await get_group_args(session=self._session, flag=group)
        return {item.pkey: item.pval for item in items}


__all__ = ["SQLModelSystemParameterRepository"]
