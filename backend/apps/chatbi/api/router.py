"""ChatBI 对外问数路由的唯一聚合入口。"""

from fastapi import APIRouter

from apps.chatbi.api import conversations, interactions, queries
from apps.chatbi.api.legacy_composition import configure_legacy_agent_cleanup
from apps.chatbi.repository.sqlmodel.agent_run_repository import (
    AgentExecutionDeletionService,
)


def compose_chatbi_router(
    *,
    graph_router: APIRouter,
) -> APIRouter:
    """聚合旧 Chat、Agent 与 Graph 路由，同时保持各自既有路径。"""

    configure_legacy_agent_cleanup(AgentExecutionDeletionService)
    router = APIRouter()
    router.include_router(conversations.router)
    router.include_router(queries.router)
    router.include_router(interactions.router)
    # 执行器尚未迁入 ChatBI，由最外层传入路由，避免反向导入具体实现。
    router.include_router(graph_router)
    return router


__all__ = ["compose_chatbi_router"]
