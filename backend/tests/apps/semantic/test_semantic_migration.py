import importlib.util
import re
from pathlib import Path

MIGRATION_PATH = Path(__file__).parents[3] / "alembic" / "versions" / "067_semantic_metric_dimension.py"


def load_migration_module():
    spec = importlib.util.spec_from_file_location("semantic_metric_dimension_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_points_to_current_head():
    migration = load_migration_module()

    assert migration.revision == "067_semantic_metric_dimension"
    assert migration.down_revision == "8adc3a4919be"


def test_migration_creates_required_tables_and_indexes_without_cross_module_foreign_keys():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for table_name in [
        "semantic_metric",
        "semantic_dimension",
        "semantic_dimension_value",
        "semantic_asset_audit",
        "chat_record_semantic_asset",
    ]:
        assert re.search(rf"op\.create_table\(\s*['\"]{table_name}['\"]", source)
        assert f"op.drop_table('{table_name}')" in source

    for index_name in [
        "ux_semantic_metric_name",
        "idx_semantic_metric_ds_status",
        "idx_semantic_metric_source",
        "idx_semantic_metric_updated",
        "ux_semantic_dimension_name",
        "idx_semantic_dimension_ds_status",
        "idx_semantic_dimension_source",
        "idx_semantic_dimension_type",
        "ux_semantic_dimension_value",
        "idx_semantic_dimension_value_enabled",
        "idx_semantic_asset_audit_asset",
        "ux_chat_record_semantic_asset",
        "idx_chat_record_semantic_asset_record",
    ]:
        assert index_name in source

    assert "ForeignKey" not in source
