"""权限变量约束迁移链路测试。"""

import importlib.util
from pathlib import Path


def test_data_policy_constraint_migration_follows_assistant_migration() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "093_access_policy_constraints.py"
    )
    spec = importlib.util.spec_from_file_location(
        "access_data_policy_constraint_migration",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "093_access_policy_constraints"
    assert module.down_revision == "092_assistant_constraints"
