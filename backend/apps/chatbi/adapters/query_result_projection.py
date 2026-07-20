from sqlmodel import Session

from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.services import QueryResultProjectionService


def build_query_result_projection_service(
    session: Session,
) -> QueryResultProjectionService:
    """装配旧 Chat 查询结果投影所需的 ChatRecord 仓储。"""

    return QueryResultProjectionService(build_chat_record_service(session))


__all__ = ["build_query_result_projection_service"]
