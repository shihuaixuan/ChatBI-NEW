from sqlalchemy import delete
from sqlmodel import Session

from apps.chatbi.models import (
    ChatRecord,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ChatRecordStatus,
)
from apps.chatbi.repository.sqlmodel import SQLModelChatRecordRepository
from apps.chatbi.services import ChatRecordService
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
                trace_id="record-repository-test",
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
