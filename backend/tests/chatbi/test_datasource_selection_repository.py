from datetime import datetime

from sqlalchemy import delete
from sqlmodel import Session

from apps.chatbi.models import Chat, ChatRecord
from apps.chatbi.repository.sqlmodel import SQLModelChatRecordRepository
from apps.chatbi.services.conversation import ChatRecordService
from common.core.db import engine


def test_datasource_binding_updates_chat_and_record_in_same_session():
    creator_id = 99503
    with Session(engine) as session:
        session.execute(delete(ChatRecord).where(ChatRecord.create_by == creator_id))
        session.execute(delete(Chat).where(Chat.create_by == creator_id))
        session.commit()
        chat = Chat(
            oid=1,
            create_time=datetime.now(),
            create_by=creator_id,
            brief="数据源绑定测试",
            chat_type="chat",
            engine_type="",
        )
        session.add(chat)
        session.flush()
        record = ChatRecord(
            chat_id=chat.id or 0,
            create_time=datetime.now(),
            create_by=creator_id,
            question="查询销售额",
            execution_type="graph",
            status="created",
            finish=False,
        )
        session.add(record)
        session.flush()

        service = ChatRecordService(SQLModelChatRecordRepository(session))
        service.bind_datasource_selection_by_id(
            record.id or 0,
            datasource_id=99803,
            record_engine_type="PostgreSQL 16",
            conversation_engine_type="PostgreSQL",
            answer='{"content":"{\\"id\\":99803}"}',
        )
        session.commit()
        session.refresh(chat)
        session.refresh(record)

        assert chat.datasource == 99803
        assert chat.engine_type == "PostgreSQL"
        assert record.datasource == 99803
        assert record.engine_type == "PostgreSQL 16"
        assert record.datasource_select_answer == (
            '{"content":"{\\"id\\":99803}"}'
        )

        session.delete(record)
        session.delete(chat)
        session.commit()
