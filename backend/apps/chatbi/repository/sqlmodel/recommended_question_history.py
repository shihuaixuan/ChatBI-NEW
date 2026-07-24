from sqlmodel import Session

from apps.conversation.composition import build_chat_record_service


class SQLModelRecommendedQuestionHistoryRepository:
    """从历史成功记录读取推荐问题去重上下文。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_recent(
        self,
        datasource_id: int | None,
        *,
        limit: int = 20,
    ) -> list[str]:
        if datasource_id is None or limit <= 0:
            return []
        return build_chat_record_service(self._session).list_recent_questions(
            datasource_id,
            limit,
        )


__all__ = ["SQLModelRecommendedQuestionHistoryRepository"]
