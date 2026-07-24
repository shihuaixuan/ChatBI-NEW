"""Conversation 对外公开的工作空间资源范围。"""

from sqlmodel import Session, select

from apps.conversation.models import Chat
from common.core.db import engine


class ConversationResourceScope:
    """读取指定工作空间可引用的会话 ID。"""

    async def list_resource_ids(self, workspace_id: int) -> list[int]:
        with Session(engine) as session:
            resource_ids = session.exec(
                select(Chat.id).where(Chat.oid == workspace_id)
            ).all()
        return [resource_id for resource_id in resource_ids if resource_id is not None]


__all__ = ["ConversationResourceScope"]
