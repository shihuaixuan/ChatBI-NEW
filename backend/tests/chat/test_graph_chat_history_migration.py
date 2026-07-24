from pathlib import Path

from apps.conversation import ChatRecordResult
from apps.conversation.models import ChatRecord

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
    # 数据库必须拒绝只绑定 chat_id 或只绑定 record_id 的半绑定状态。
    assert 'op.create_check_constraint(' in content
    assert '"ck_workflow_run_chat_ownership"' in content
    assert "chat_id IS NULL AND record_id IS NULL" in content
    assert "chat_id IS NOT NULL AND record_id IS NOT NULL" in content
    assert 'op.create_table("workflow_artifact_cleanup"' in content
    assert "DELETE FROM chat" not in content
    assert "UPDATE workflow_run" not in content


def test_chat_record_declares_execution_type_contract():
    # 085 迁移后新记录默认使用 Graph，响应模型仍兼容未携带该字段的历史调用方。
    column = ChatRecord.__table__.c.execution_type

    assert column.nullable is False
    assert column.default.arg == "graph"
    assert ChatRecordResult().execution_type is None
