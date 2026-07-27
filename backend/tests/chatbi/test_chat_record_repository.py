from datetime import datetime

from sqlalchemy import delete
from sqlmodel import Session

from apps.conversation import (
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordService,
    ChatRecordStatus,
)
from apps.conversation.models import (
    Chat,
    ChatRecord,
)
from apps.conversation.repository.sqlmodel import SQLModelChatRecordRepository
from common.core.db import engine


def test_sqlmodel_chat_record_repository_persists_canonical_lifecycle():
    with Session(engine) as session:
        session.execute(delete(ChatRecord).where(ChatRecord.create_by == 99501))
        session.commit()
        service = ChatRecordService(SQLModelChatRecordRepository(session))

        record = service.create(
            ChatRecordCreateData(
                chat_id=99601,
                user_id=99501,
                question="仓储状态测试",
                dataset_id=99701,
                datasource_id=99801,
                engine_type="PostgreSQL",
                execution_type=ChatRecordExecutionType.AGENT,
                run_id="record-repository-test",
            )
        )
        service.transition(
            record,
            ChatRecordStatus.FAILED,
            error="首次执行失败",
        )
        session.commit()

        stored = session.get(ChatRecord, record.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.finish is True
        assert stored.finish_time is not None
        assert stored.run_id == "record-repository-test"

        service.transition(stored, ChatRecordStatus.RUNNING)
        service.transition(
            stored,
            ChatRecordStatus.SUCCEEDED,
            result=ChatRecordResultProjection(
                answer="执行成功",
                sql="select 1",
            ),
        )
        session.commit()
        session.refresh(stored)

        assert stored.status == "succeeded"
        assert stored.finish is True
        assert stored.error is None
        assert stored.sql_answer == "执行成功"
        assert stored.sql == "select 1"

        session.delete(stored)
        session.commit()


def test_sqlmodel_chat_record_repository_promotes_extended_recommendation():
    with Session(engine) as session:
        session.execute(delete(ChatRecord).where(ChatRecord.create_by == 99502))
        session.execute(delete(Chat).where(Chat.create_by == 99502))
        session.commit()
        chat = Chat(
            oid=1,
            create_time=datetime.now(),
            create_by=99502,
            brief="推荐投影测试",
            chat_type="chat",
            dataset_id=99702,
            datasource=99802,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.flush()
        service = ChatRecordService(SQLModelChatRecordRepository(session))
        record = service.create(
            ChatRecordCreateData(
                chat_id=chat.id or 0,
                user_id=99502,
                question="推荐问题",
                dataset_id=99702,
                datasource_id=99802,
                engine_type="PostgreSQL",
                execution_type=ChatRecordExecutionType.GRAPH,
            )
        )

        service.project_recommendation_by_id(
            record.id or 0,
            answer='{"content":"[\\"问题一\\"]"}',
            questions='["问题一"]',
            articles_number=5,
        )
        session.commit()
        session.refresh(chat)

        assert chat.recommended_question_answer == '{"content":"[\\"问题一\\"]"}'
        assert chat.recommended_question == '["问题一"]'
        assert chat.recommended_generate is True

        session.delete(record)
        session.delete(chat)
        session.commit()
