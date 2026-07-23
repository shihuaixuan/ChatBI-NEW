from typing import Any

from sqlmodel import Session, col, select

from apps.platform_config.models import SysArgModel

DEFAULT_CHAT_ARGS = {
    "chat.expand_thinking_block": "false",
    "chat.limit_rows": "true",
    "chat.context_record_count": "3",
}

class SQLModelPlatformParameterRepository:
    """基于现有平台参数存储的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_models(self, group: str | None = None) -> list[SysArgModel]:
        statement = select(SysArgModel)
        if group:
            statement = statement.where(
                col(SysArgModel.pkey).startswith(f"{group}.")
            )
        else:
            statement = statement.where(
                ~col(SysArgModel.pkey).startswith("appearance.")
            )
        items = list(
            self._session.exec(
                statement.order_by(
                    col(SysArgModel.sort_no),
                    col(SysArgModel.id),
                )
            ).all()
        )
        if group in {None, "chat"}:
            existing_keys = {item.pkey for item in items}
            items.extend(
                SysArgModel(pkey=pkey, pval=pval)
                for pkey, pval in DEFAULT_CHAT_ARGS.items()
                if pkey not in existing_keys
            )
        return items

    def save_models(
        self,
        sys_args: list[SysArgModel],
        file_mapping: dict[str, Any] | None = None,
    ) -> None:
        existing = {
            item.pkey: item
            for item in self._session.exec(
                select(SysArgModel).where(
                    col(SysArgModel.pkey).in_(
                        [item.pkey for item in sys_args]
                    )
                )
            ).all()
        }
        for item in sys_args:
            current = existing.get(item.pkey)
            pval = item.pval
            if item.ptype == "file" and file_mapping:
                pval = file_mapping.get(item.pkey, pval)
            if current is None:
                self._session.add(
                    SysArgModel(
                        pkey=item.pkey,
                        pval=pval,
                        ptype=item.ptype,
                        sort_no=item.sort_no,
                    )
                )
                continue
            current.pval = pval
            current.ptype = item.ptype
            current.sort_no = item.sort_no
            self._session.add(current)
        self._session.commit()

    async def list_group(self, group: str) -> dict[str, str | None]:
        items = self.list_models(group)
        return {item.pkey: item.pval for item in items}


__all__ = ["SQLModelPlatformParameterRepository"]
