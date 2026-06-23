from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "076_chat_headless_dataset.py"


def test_migration_deletes_old_chat_data_before_schema_change():
    content = MIGRATION.read_text(encoding="utf-8")

    assert "DELETE FROM chat_log" in content
    assert "type = '0'" in content
    assert "DELETE FROM chat_record" in content
    assert "DELETE FROM chat" in content


def test_migration_adds_dataset_columns_and_indexes():
    content = MIGRATION.read_text(encoding="utf-8")

    assert 'op.add_column("chat", sa.Column("dataset_id"' in content
    assert 'op.add_column("chat_record", sa.Column("dataset_id"' in content
    assert "idx_chat_dataset" in content
    assert "idx_chat_record_dataset" in content
