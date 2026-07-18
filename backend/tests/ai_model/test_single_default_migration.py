from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/089_ai_model_single_default.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_089_ai_model_single_default",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _CountResult:
    def __init__(self, counts: tuple[int, int]) -> None:
        self._counts = counts

    def one(self) -> tuple[int, int]:
        return self._counts


class _Connection:
    def __init__(self, counts: tuple[int, int]) -> None:
        self._counts = counts

    def execute(self, _statement: Any) -> _CountResult:
        return _CountResult(self._counts)


@pytest.mark.parametrize("counts", [(2, 0), (2, 2)])
def test_upgrade_rejects_invalid_existing_default_count(
    monkeypatch: pytest.MonkeyPatch,
    counts: tuple[int, int],
) -> None:
    migration = _load_migration()
    created_indexes: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection(counts))
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, *_args, **_kwargs: created_indexes.append(name),
    )

    with pytest.raises(RuntimeError, match="默认模型数据不满足唯一约束"):
        migration.upgrade()

    assert created_indexes == []


@pytest.mark.parametrize("counts", [(0, 0), (2, 1)])
def test_upgrade_creates_partial_unique_index_for_valid_data(
    monkeypatch: pytest.MonkeyPatch,
    counts: tuple[int, int],
) -> None:
    migration = _load_migration()
    created_indexes: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection(counts))
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, *_args, **_kwargs: created_indexes.append(name),
    )

    migration.upgrade()

    assert created_indexes == ["ux_ai_model_single_default"]
