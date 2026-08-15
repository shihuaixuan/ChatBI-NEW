"""用户记忆持久化端口。"""

from datetime import datetime
from typing import Protocol

from apps.memory.models.dto import (
    MemoryEvidenceRecord,
    MemoryRecord,
    MemoryUsageMetrics,
    MemoryUsageRecord,
)


class MemoryRepository(Protocol):
    """面向用户归属的记忆仓储端口。"""

    def list_active(
        self,
        oid: int,
        user_id: int,
        *,
        layer: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]: ...

    def get(self, oid: int, user_id: int, memory_id: int) -> MemoryRecord | None: ...

    def list_by_key(
        self,
        oid: int,
        user_id: int,
        memory_type: str,
        memory_key: str,
    ) -> list[MemoryRecord]: ...

    def has_evidence_source_session(
        self,
        oid: int,
        user_id: int,
        memory_id: int,
        source_session_id: str,
    ) -> bool: ...

    def upsert_embedding(
        self,
        oid: int,
        user_id: int,
        memory_id: int,
        embedding_profile: str,
        provider: str,
        model: str,
        vector: list[float],
    ) -> None: ...

    def search_dense(
        self,
        oid: int,
        user_id: int,
        embedding_profile: str,
        vector: list[float],
        limit: int,
    ) -> list[MemoryRecord]: ...

    def create_usage(self, usage: MemoryUsageRecord) -> MemoryUsageRecord: ...

    def summarize_usage(
        self,
        oid: int,
        user_id: int,
        *,
        limit: int = 100,
    ) -> MemoryUsageMetrics: ...

    def get_usage(
        self,
        oid: int,
        user_id: int,
        usage_id: int,
    ) -> MemoryUsageRecord | None: ...

    def mark_usage_adopted(
        self,
        oid: int,
        user_id: int,
        usage_id: int,
        adopted: bool,
        adopted_at: datetime,
    ) -> MemoryUsageRecord | None: ...

    def create(self, record: MemoryRecord) -> MemoryRecord: ...

    def update(self, record: MemoryRecord) -> MemoryRecord: ...

    def delete(self, oid: int, user_id: int, memory_id: int) -> bool: ...

    def create_evidence(
        self,
        evidence: MemoryEvidenceRecord,
    ) -> MemoryEvidenceRecord: ...

    def commit(self) -> None: ...


__all__ = ["MemoryRepository"]
