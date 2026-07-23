from sqlmodel import Session, col, select

from apps.chatbi.models import ChatRecord


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
        statement = (
            select(ChatRecord.question)
            .where(
                ChatRecord.datasource == datasource_id,
                col(ChatRecord.question).is_not(None),
                col(ChatRecord.error).is_(None),
            )
            .order_by(col(ChatRecord.create_time).desc())
            .limit(limit)
        )
        return [
            question
            for question in self._session.exec(statement).all()
            if isinstance(question, str) and question.strip()
        ]


__all__ = ["SQLModelRecommendedQuestionHistoryRepository"]
