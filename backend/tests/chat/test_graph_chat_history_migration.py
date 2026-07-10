from pathlib import Path

from apps.chat.models.chat_model import ChatRecord, ChatRecordResult


# 固定检查本任务新增迁移，防止误把破坏性数据改写带入历史归属升级。
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "078_graph_chat_history.py"
)


def test_graph_chat_history_migration_adds_non_destructive_schema():
    # 迁移只允许回填 ChatRecord 的执行类型，不得推断既有 WorkflowRun 的归属。
    content = MIGRATION.read_text()

    assert 'revision = "078_graph_chat_history"' in content
    assert 'down_revision = "077_headless_metric_embedding"' in content
    assert 'op.add_column("chat_record"' in content
    assert '"execution_type"' in content
    assert 'op.add_column("workflow_run"' in content
    assert '"chat_id"' in content
    assert '"record_id"' in content
    assert 'op.create_table("workflow_artifact_cleanup"' in content
    assert "DELETE FROM chat" not in content
    assert "UPDATE workflow_run" not in content


def test_chat_record_declares_execution_type_contract():
    # 持久化模型默认标记旧执行链，响应模型则兼容尚未携带该字段的调用方。
    column = ChatRecord.__table__.c.execution_type

    assert column.nullable is False
    assert column.default.arg == "legacy"
    assert ChatRecordResult().execution_type is None
