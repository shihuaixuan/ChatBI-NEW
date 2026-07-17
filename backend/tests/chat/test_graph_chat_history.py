"""验证 Graph ChatRecord 快照可以直接从聊天历史读取。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from apps.chat.models.chat_model import Chat, ChatRecord
from common.core.db import engine


@pytest.mark.parametrize("with_data", [False, True])
def test_get_chat_with_records_returns_graph_snapshot(monkeypatch, with_data):
    """历史接口应直接返回 Graph 快照及执行归属字段。"""

    from tests.chat.test_semantic_dataset_chat import import_chat_crud

    chat_crud = import_chat_crud(monkeypatch)
    current_user = SimpleNamespace(id=9101, oid=9201)
    now = datetime.now()
    with Session(engine) as session:
        chat = Chat(
            oid=current_user.oid,
            create_by=current_user.id,
            create_time=now,
            brief="graph-history-test",
            chat_type="chat",
            dataset_id=None,
            datasource=None,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.flush()
        record = ChatRecord(
            chat_id=chat.id or 0,
            create_by=current_user.id,
            create_time=now,
            dataset_id=None,
            datasource=None,
            engine_type=chat.engine_type,
            execution_type="graph",
            question="本月销售额",
            sql_answer="本月销售额为 100 元。",
            sql="select 100 as sales",
            status="succeeded",
            trace_id="graph-history-1",
            finish=True,
        )
        session.add(record)
        session.commit()

        result = chat_crud.get_chat_with_records(
            session=session,
            chart_id=chat.id or 0,
            current_user=current_user,
            current_assistant=None,
            with_data=with_data,
        )

        graph_record = next(item for item in result.records if item["trace_id"] == "graph-history-1")
        assert graph_record["execution_type"] == "graph"
        assert graph_record["status"] == "succeeded"
        assert graph_record["sql_answer"] == "本月销售额为 100 元。"

        session.delete(record)
        session.delete(chat)
        session.commit()
