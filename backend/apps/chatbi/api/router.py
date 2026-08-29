"""ChatBI 对外问数路由的唯一聚合入口。"""

from fastapi import APIRouter

from apps.chatbi.api import conversations, interactions, queries
from apps.chatbi.composition import configure_agent_cleanup
from apps.chatbi.repository.sqlmodel.agent_run_repository import (
    AgentExecutionDeletionService,
)


def compose_chatbi_router() -> APIRouter:
    """聚合 Chat 与 Agent 路由，保持各自既有路径。"""

    configure_agent_cleanup(AgentExecutionDeletionService)
    router = APIRouter()
    router.include_router(conversations.router)
    router.include_router(queries.router)
    router.include_router(interactions.router)
    return router


__all__ = ["compose_chatbi_router"]
