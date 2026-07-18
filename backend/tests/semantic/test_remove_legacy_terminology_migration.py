from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/088_remove_legacy_terminology.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_088_remove_legacy_terminology",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _Connection:
    def __init__(self, value: int) -> None:
        self._value = value

    def execute(self, _statement: Any) -> _ScalarResult:
        return _ScalarResult(self._value)


def test_upgrade_rejects_non_empty_legacy_table(monkeypatch: pytest.MonkeyPatch):
    migration = _load_migration()
    dropped_tables: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection(2))
    monkeypatch.setattr(migration.op, "drop_table", dropped_tables.append)

    with pytest.raises(RuntimeError, match="仍有 2 条记录"):
        migration.upgrade()

    assert dropped_tables == []


def test_upgrade_drops_empty_legacy_table(monkeypatch: pytest.MonkeyPatch):
    migration = _load_migration()
    dropped_tables: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection(0))
    monkeypatch.setattr(migration.op, "drop_table", dropped_tables.append)

    migration.upgrade()

    assert dropped_tables == ["terminology"]
