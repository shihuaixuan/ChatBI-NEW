from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session

from apps.chatbi.repository.sqlmodel import (
    SQLModelRecommendedQuestionHistoryRepository,
)
from apps.conversation.models import ChatRecord
from common.core.db import engine


def test_history_provider_returns_recent_successful_questions_only():
    datasource_id = 99881
    now = datetime.now()
    with Session(engine) as session:
        session.execute(
            delete(ChatRecord).where(ChatRecord.datasource == datasource_id)
        )
        session.add_all(
            [
                ChatRecord(
                    chat_id=1,
                    create_by=1,
                    datasource=datasource_id,
                    question="较早问题",
                    create_time=now - timedelta(minutes=2),
                    execution_type="graph",
                ),
                ChatRecord(
                    chat_id=1,
                    create_by=1,
                    datasource=datasource_id,
                    question="最新问题",
                    create_time=now,
                    execution_type="graph",
                ),
                ChatRecord(
                    chat_id=1,
                    create_by=1,
                    datasource=datasource_id,
                    question="失败问题",
                    error="执行失败",
                    create_time=now + timedelta(minutes=1),
                    execution_type="graph",
                ),
                ChatRecord(
                    chat_id=1,
                    create_by=1,
                    datasource=datasource_id,
                    question="   ",
                    create_time=now + timedelta(minutes=2),
                    execution_type="graph",
                ),
            ]
        )
        session.commit()

        provider = SQLModelRecommendedQuestionHistoryRepository(session)

        assert provider.list_recent(datasource_id, limit=2) == [
            "最新问题",
        ]
        assert provider.list_recent(None) == []
        assert provider.list_recent(datasource_id, limit=0) == []

        session.execute(
            delete(ChatRecord).where(ChatRecord.datasource == datasource_id)
        )
        session.commit()
