from __future__ import annotations

import time
from dataclasses import dataclass

from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.models import RuntimeSchema


@dataclass
class _CacheEntry:
    value: RuntimeSchema
    expires_at: float | None


class RuntimeAssetCache:
    def __init__(self, ttl_seconds: int | None = None):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, _CacheEntry] = {}

    def get(self, scope: DatasetScope) -> RuntimeSchema | None:
        key = self.build_key(scope)
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return entry.value

    def set(self, scope: DatasetScope, value: RuntimeSchema) -> None:
        expires_at = time.monotonic() + self.ttl_seconds if self.ttl_seconds else None
        self._items[self.build_key(scope)] = _CacheEntry(value=value, expires_at=expires_at)

    def invalidate_scope(self, scope: DatasetScope) -> int:
        key = self.build_key(scope)
        existed = key in self._items
        self._items.pop(key, None)
        return 1 if existed else 0

    def invalidate_dataset(self, dataset_id: int | str | None) -> int:
        if dataset_id is None:
            return 0
        prefix = f"{dataset_id}:"
        keys = [key for key in self._items if key.startswith(prefix)]
        for key in keys:
            self._items.pop(key, None)
        return len(keys)

    def clear(self) -> int:
        size = len(self._items)
        self._items.clear()
        return size

    def build_key(self, scope: DatasetScope) -> str:
        table_text = ",".join(str(table_id) for table_id in sorted(scope.table_ids)) or "all"
        return f"{scope.dataset_id}:oid={scope.oid}:ds={scope.datasource_id}:tables={table_text}"


runtime_asset_cache = RuntimeAssetCache()
