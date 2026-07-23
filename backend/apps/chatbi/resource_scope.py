"""ChatBI 对外公开的工作空间资源范围读取能力。"""

from sqlmodel import Session, select

from apps.chatbi.models import Chat
from common.core.db import engine


class ChatWorkspaceResourceScopeReader:
    """只公开当前工作空间可引用的会话 ID。"""

    async def list_resource_ids(self, workspace_id: int) -> list[int]:
        with Session(engine) as session:
            resource_ids = session.exec(
                select(Chat.id).where(Chat.oid == workspace_id)
            ).all()
        return [resource_id for resource_id in resource_ids if resource_id is not None]
