"""基于 SQLModel 的用户记忆仓储。"""

from datetime import datetime

from sqlalchemy import delete, text
from sqlmodel import Session, col, select

from apps.memory.models.dto import (
    MemoryEvidenceRecord,
    MemoryRecallVariant,
    MemoryRecord,
    MemoryUsageMetrics,
    MemoryUsageRecord,
    MemoryUsageSummary,
    MemoryVariantUsageMetrics,
)
from apps.memory.models.orm import (
    ChatbiMemory,
    ChatbiMemoryEmbedding,
    ChatbiMemoryEvidence,
    ChatbiMemoryUsage,
)


class SQLModelMemoryRepository:
    """用户记忆 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(model: ChatbiMemory) -> MemoryRecord:
        if model.id is None:
            raise RuntimeError("MEMORY_ID_MISSING")
        return MemoryRecord.model_validate(model.model_dump())

    @staticmethod
    def _evidence_record(model: ChatbiMemoryEvidence) -> MemoryEvidenceRecord:
        if model.id is None:
            raise RuntimeError("MEMORY_EVIDENCE_ID_MISSING")
        return MemoryEvidenceRecord.model_validate(model.model_dump())

    def list_active(
        self,
        oid: int,
        user_id: int,
        *,
        layer: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        statement = select(ChatbiMemory).where(
            col(ChatbiMemory.oid) == oid,
            col(ChatbiMemory.user_id) == user_id,
            col(ChatbiMemory.status) == "active",
        )
        if layer is not None:
            statement = statement.where(col(ChatbiMemory.layer) == layer)
        rows = self._session.exec(
            statement.order_by(
                col(ChatbiMemory.last_confirmed_at).desc(),
                col(ChatbiMemory.updated_at).desc(),
                col(ChatbiMemory.id).desc(),
            ).limit(limit)
        ).all()
        return [self._record(row) for row in rows]

    def get(self, oid: int, user_id: int, memory_id: int) -> MemoryRecord | None:
        row = self._session.exec(
            select(ChatbiMemory).where(
                col(ChatbiMemory.id) == memory_id,
                col(ChatbiMemory.oid) == oid,
                col(ChatbiMemory.user_id) == user_id,
            )
        ).first()
        return self._record(row) if row is not None else None

    def list_by_key(
        self,
        oid: int,
        user_id: int,
        memory_type: str,
        memory_key: str,
    ) -> list[MemoryRecord]:
        rows = self._session.exec(
            select(ChatbiMemory)
            .where(
                col(ChatbiMemory.oid) == oid,
                col(ChatbiMemory.user_id) == user_id,
                col(ChatbiMemory.memory_type) == memory_type,
                col(ChatbiMemory.memory_key) == memory_key,
            )
            .order_by(col(ChatbiMemory.updated_at).desc(), col(ChatbiMemory.id).desc())
        ).all()
        return [self._record(row) for row in rows]

    def has_evidence_source_session(
        self,
        oid: int,
        user_id: int,
        memory_id: int,
        source_session_id: str,
    ) -> bool:
        row = self._session.exec(
            select(ChatbiMemoryEvidence.id).where(
                col(ChatbiMemoryEvidence.memory_id) == memory_id,
                col(ChatbiMemoryEvidence.oid) == oid,
                col(ChatbiMemoryEvidence.user_id) == user_id,
                col(ChatbiMemoryEvidence.source_session_id) == source_session_id,
            )
        ).first()
        return row is not None

    def upsert_embedding(
        self,
        oid: int,
        user_id: int,
        memory_id: int,
        embedding_profile: str,
        provider: str,
        model: str,
        vector: list[float],
    ) -> None:
        embedding = self._session.exec(
            select(ChatbiMemoryEmbedding).where(
                col(ChatbiMemoryEmbedding.oid) == oid,
                col(ChatbiMemoryEmbedding.user_id) == user_id,
                col(ChatbiMemoryEmbedding.memory_id) == memory_id,
                col(ChatbiMemoryEmbedding.embedding_profile) == embedding_profile,
            )
        ).first()
        if embedding is None:
            embedding = ChatbiMemoryEmbedding(
                memory_id=memory_id,
                oid=oid,
                user_id=user_id,
                embedding_profile=embedding_profile,
                provider=provider,
                model=model,
                dimension=len(vector),
                embedding=vector,
                status="active",
            )
        else:
            embedding.provider = provider
            embedding.model = model
            embedding.dimension = len(vector)
            embedding.embedding = vector
            embedding.status = "active"
        self._session.add(embedding)
        self._session.flush()

    def search_dense(
        self,
        oid: int,
        user_id: int,
        embedding_profile: str,
        vector: list[float],
        limit: int,
    ) -> list[MemoryRecord]:
        if limit <= 0:
            raise ValueError("MEMORY_DENSE_LIMIT_INVALID")
        rows = self._session.execute(
            text(
                """
                SELECT memory.*
                FROM chatbi_memory AS memory
                JOIN chatbi_memory_embedding AS embedding
                  ON embedding.memory_id = memory.id
                 AND embedding.oid = memory.oid
                 AND embedding.user_id = memory.user_id
                WHERE memory.oid = :oid
                  AND memory.user_id = :user_id
                  AND memory.status = 'active'
                  AND (memory.expires_at IS NULL OR memory.expires_at > CURRENT_TIMESTAMP)
                  AND embedding.embedding_profile = :embedding_profile
                  AND embedding.status = 'active'
                  AND embedding.embedding IS NOT NULL
                ORDER BY embedding.embedding <=> CAST(:query_vector AS vector)
                LIMIT :limit
                """
            ),
            {
                "oid": oid,
                "user_id": user_id,
                "embedding_profile": embedding_profile,
                "query_vector": str(vector),
                "limit": limit,
            },
        ).mappings().all()
        return [MemoryRecord.model_validate(dict(row)) for row in rows]

    def create(self, record: MemoryRecord) -> MemoryRecord:
        model = ChatbiMemory(**record.model_dump(exclude={"id"}))
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return self._record(model)

    def update(self, record: MemoryRecord) -> MemoryRecord:
        model = self._session.exec(
            select(ChatbiMemory).where(
                col(ChatbiMemory.id) == record.id,
                col(ChatbiMemory.oid) == record.oid,
                col(ChatbiMemory.user_id) == record.user_id,
            )
        ).first()
        if model is None:
            raise ValueError("MEMORY_NOT_FOUND")
        for field in (
            "layer",
            "memory_type",
            "memory_key",
            "statement",
            "payload",
            "confidence",
            "evidence_count",
            "session_count",
            "version",
            "status",
            "last_confirmed_at",
            "last_used_at",
            "expires_at",
            "updated_at",
        ):
            setattr(model, field, getattr(record, field))
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return self._record(model)

    def delete(self, oid: int, user_id: int, memory_id: int) -> bool:
        memory = self._session.exec(
            select(ChatbiMemory).where(
                col(ChatbiMemory.id) == memory_id,
                col(ChatbiMemory.oid) == oid,
                col(ChatbiMemory.user_id) == user_id,
            )
        ).first()
        if memory is None:
            return False
        self._session.exec(
            delete(ChatbiMemoryEvidence).where(
                col(ChatbiMemoryEvidence.memory_id) == memory_id,
                col(ChatbiMemoryEvidence.oid) == oid,
                col(ChatbiMemoryEvidence.user_id) == user_id,
            )
        )
        self._session.delete(memory)
        self._session.flush()
        return True

    def create_evidence(self, evidence: MemoryEvidenceRecord) -> MemoryEvidenceRecord:
        model = ChatbiMemoryEvidence(**evidence.model_dump(exclude={"id"}))
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return self._evidence_record(model)

    @staticmethod
    def _usage_record(model: ChatbiMemoryUsage) -> MemoryUsageRecord:
        if model.id is None:
            raise RuntimeError("MEMORY_USAGE_ID_MISSING")
        return MemoryUsageRecord.model_validate(model.model_dump())

    def create_usage(self, usage: MemoryUsageRecord) -> MemoryUsageRecord:
        model = ChatbiMemoryUsage(**usage.model_dump(exclude={"id"}))
        self._session.add(model)
        self._session.flush()
        self._session.refresh(model)
        return self._usage_record(model)

    def summarize_usage(
        self,
        oid: int,
        user_id: int,
        *,
        limit: int = 100,
    ) -> MemoryUsageMetrics:
        if limit <= 0 or limit > 100:
            raise ValueError("MEMORY_USAGE_METRICS_LIMIT_INVALID")
        summary = self._session.execute(
            text(
                """
                SELECT
                    COUNT(DISTINCT memory.id) FILTER (WHERE memory.status = 'active') AS active_memory_count,
                    COUNT(DISTINCT memory.id) FILTER (WHERE memory.status = 'active' AND memory.layer = 'profile') AS profile_count,
                    COUNT(DISTINCT memory.id) FILTER (WHERE memory.status = 'active' AND memory.layer = 'scenario') AS scenario_count,
                    COUNT(DISTINCT memory.id) FILTER (WHERE memory.status = 'active' AND memory.layer = 'atom') AS atom_count,
                    COUNT(usage.id) AS usage_count,
                    COUNT(DISTINCT usage.session_id) AS unique_session_count,
                    COUNT(DISTINCT usage.run_id) AS unique_run_count,
                    COUNT(*) FILTER (WHERE usage.adopted IS TRUE) AS adopted_count,
                    COUNT(*) FILTER (WHERE usage.adopted IS NOT NULL) AS evaluated_count,
                    MAX(usage.created_at) AS last_used_at
                FROM chatbi_memory AS memory
                LEFT JOIN chatbi_memory_usage AS usage
                  ON usage.memory_id = memory.id
                 AND usage.oid = memory.oid
                 AND usage.user_id = memory.user_id
                WHERE memory.oid = :oid
                  AND memory.user_id = :user_id
                """
            ),
            {"oid": oid, "user_id": user_id},
        ).mappings().one()
        usage_count = int(summary["usage_count"] or 0)
        evaluated_count = int(summary["evaluated_count"] or 0)
        adopted_count = int(summary["adopted_count"] or 0)
        return MemoryUsageMetrics(
            active_memory_count=int(summary["active_memory_count"] or 0),
            profile_count=int(summary["profile_count"] or 0),
            scenario_count=int(summary["scenario_count"] or 0),
            atom_count=int(summary["atom_count"] or 0),
            usage_count=usage_count,
            unique_session_count=int(summary["unique_session_count"] or 0),
            unique_run_count=int(summary["unique_run_count"] or 0),
            adopted_count=adopted_count,
            evaluated_count=evaluated_count,
            adoption_rate=(adopted_count / evaluated_count if evaluated_count else None),
            last_used_at=summary["last_used_at"],
            items=self._usage_summaries(oid, user_id, limit),
            by_variant=self._variant_usage_summaries(oid, user_id),
        )

    def _usage_summaries(
        self,
        oid: int,
        user_id: int,
        limit: int,
    ) -> list[MemoryUsageSummary]:
        rows = self._session.execute(
            text(
                """
                SELECT
                    usage.memory_id,
                    COUNT(*) AS usage_count,
                    COUNT(DISTINCT usage.session_id) AS unique_session_count,
                    COUNT(DISTINCT usage.run_id) AS unique_run_count,
                    COUNT(*) FILTER (WHERE usage.adopted IS TRUE) AS adopted_count,
                    COUNT(*) FILTER (WHERE usage.adopted IS NOT NULL) AS evaluated_count,
                    MAX(usage.created_at) AS last_used_at
                FROM chatbi_memory_usage AS usage
                JOIN chatbi_memory AS memory
                  ON memory.id = usage.memory_id
                 AND memory.oid = usage.oid
                 AND memory.user_id = usage.user_id
                WHERE usage.oid = :oid
                  AND usage.user_id = :user_id
                GROUP BY usage.memory_id
                ORDER BY MAX(usage.created_at) DESC NULLS LAST, usage.memory_id DESC
                LIMIT :limit
                """
            ),
            {"oid": oid, "user_id": user_id, "limit": limit},
        ).mappings().all()
        summaries: list[MemoryUsageSummary] = []
        for row in rows:
            evaluated_count = int(row["evaluated_count"] or 0)
            adopted_count = int(row["adopted_count"] or 0)
            summaries.append(
                MemoryUsageSummary(
                    memory_id=int(row["memory_id"]),
                    usage_count=int(row["usage_count"] or 0),
                    unique_session_count=int(row["unique_session_count"] or 0),
                    unique_run_count=int(row["unique_run_count"] or 0),
                    adopted_count=adopted_count,
                    evaluated_count=evaluated_count,
                    adoption_rate=(
                        adopted_count / evaluated_count if evaluated_count else None
                    ),
                    last_used_at=row["last_used_at"],
                )
            )
        return summaries

    def _variant_usage_summaries(
        self,
        oid: int,
        user_id: int,
    ) -> list[MemoryVariantUsageMetrics]:
        rows = self._session.execute(
            text(
                """
                SELECT
                    recall_variant,
                    COUNT(*) AS usage_count,
                    COUNT(DISTINCT session_id) AS unique_session_count,
                    COUNT(DISTINCT run_id) AS unique_run_count,
                    COUNT(*) FILTER (WHERE adopted IS TRUE) AS adopted_count,
                    COUNT(*) FILTER (WHERE adopted IS NOT NULL) AS evaluated_count,
                    MAX(created_at) AS last_used_at
                FROM chatbi_memory_usage
                WHERE oid = :oid
                  AND user_id = :user_id
                GROUP BY recall_variant
                ORDER BY recall_variant
                """
            ),
            {"oid": oid, "user_id": user_id},
        ).mappings().all()
        metrics: list[MemoryVariantUsageMetrics] = []
        for row in rows:
            evaluated_count = int(row["evaluated_count"] or 0)
            adopted_count = int(row["adopted_count"] or 0)
            metrics.append(
                MemoryVariantUsageMetrics(
                    recall_variant=MemoryRecallVariant(row["recall_variant"]),
                    usage_count=int(row["usage_count"] or 0),
                    unique_session_count=int(row["unique_session_count"] or 0),
                    unique_run_count=int(row["unique_run_count"] or 0),
                    adopted_count=adopted_count,
                    evaluated_count=evaluated_count,
                    adoption_rate=(
                        adopted_count / evaluated_count if evaluated_count else None
                    ),
                    last_used_at=row["last_used_at"],
                )
            )
        return metrics

    def get_usage(
        self,
        oid: int,
        user_id: int,
        usage_id: int,
    ) -> MemoryUsageRecord | None:
        row = self._session.exec(
            select(ChatbiMemoryUsage).where(
                col(ChatbiMemoryUsage.id) == usage_id,
                col(ChatbiMemoryUsage.oid) == oid,
                col(ChatbiMemoryUsage.user_id) == user_id,
            )
        ).first()
        return self._usage_record(row) if row is not None else None

    def mark_usage_adopted(
        self,
        oid: int,
        user_id: int,
        usage_id: int,
        adopted: bool,
        adopted_at: datetime,
    ) -> MemoryUsageRecord | None:
        row = self._session.exec(
            select(ChatbiMemoryUsage).where(
                col(ChatbiMemoryUsage.id) == usage_id,
                col(ChatbiMemoryUsage.oid) == oid,
                col(ChatbiMemoryUsage.user_id) == user_id,
            )
        ).first()
        if row is None:
            return None
        row.adopted = adopted
        row.adopted_at = adopted_at
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        return self._usage_record(row)

    def commit(self) -> None:
        self._session.commit()


__all__ = ["SQLModelMemoryRepository"]
