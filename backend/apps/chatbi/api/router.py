"""ChatBI 对外问数路由的唯一聚合入口。"""

from fastapi import APIRouter

from apps.chatbi.api import conversations, queries


def compose_chatbi_router(
    *,
    agent_router: APIRouter,
    graph_router: APIRouter,
) -> APIRouter:
    """聚合旧 Chat、Agent 与 Graph 路由，同时保持各自既有路径。"""

    router = APIRouter()
    router.include_router(conversations.router)
    router.include_router(queries.router)
    # 执行器尚未迁入 ChatBI，由最外层传入路由，避免反向导入具体实现。
    router.include_router(agent_router)
    router.include_router(graph_router)
    return router


__all__ = ["compose_chatbi_router"]
