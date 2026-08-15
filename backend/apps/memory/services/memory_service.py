"""用户记忆应用服务。"""

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from apps.memory.errors import (
    MemoryNotFoundError,
    MemoryUsageNotFoundError,
    MemoryValidationError,
)
from apps.memory.models.dto import (
    ClarificationMemoryEvent,
    MemoryCandidateInput,
    MemoryContextItem,
    MemoryContextSnapshot,
    MemoryCreateInput,
    MemoryEvidenceRecord,
    MemoryEvidenceType,
    MemoryLayer,
    MemoryRecallAssignment,
    MemoryRecallComparison,
    MemoryRecallComparisonDecision,
    MemoryRecallVariant,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    MemoryUpdateInput,
    MemoryUsageMetrics,
    MemoryUsageRecord,
    SuccessfulQueryMemoryEvent,
)
from apps.memory.repository.memory_repository import MemoryRepository
from apps.memory.services.memory_extraction import MemoryCandidateExtractor
from apps.memory.services.memory_rules import (
    evidence_strength,
    normalize_memory_key,
    payload_fingerprint,
    validate_memory_payload,
)


class MemoryEmbeddingProvider(Protocol):
    """记忆向量索引依赖的最小 embedding 端口。"""

    provider: str
    model: str
    dimension: int

    def embed_query(self, text: str) -> list[float]: ...


class MemoryService:
    """承载用户记忆读写、合并和生命周期规则。"""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        now_factory: Callable[[], datetime] = datetime.now,
        promotion_evidence_count: int = 3,
        promotion_session_count: int = 2,
        embedding_provider: MemoryEmbeddingProvider | None = None,
        embedding_profile: str = "bge-m3-1024",
        recall_experiment_enabled: bool = False,
        recall_experiment_treatment_percent: int = 0,
        recall_experiment_salt: str = "chatbi-memory-recall-v1",
        recall_min_evaluated_per_variant: int = 100,
        recall_max_adoption_drop: float = 0.05,
        candidate_extractor: MemoryCandidateExtractor | None = None,
    ) -> None:
        if promotion_evidence_count <= 0 or promotion_session_count <= 0:
            raise ValueError("MEMORY_PROMOTION_THRESHOLD_INVALID")
        if not 0 <= recall_experiment_treatment_percent <= 100:
            raise ValueError("MEMORY_RECALL_TREATMENT_PERCENT_INVALID")
        if not recall_experiment_salt.strip():
            raise ValueError("MEMORY_RECALL_EXPERIMENT_SALT_EMPTY")
        if recall_min_evaluated_per_variant <= 0:
            raise ValueError("MEMORY_RECALL_MIN_EVALUATED_INVALID")
        if not 0 <= recall_max_adoption_drop <= 1:
            raise ValueError("MEMORY_RECALL_MAX_ADOPTION_DROP_INVALID")
        self._repository = repository
        self._now = now_factory
        self._promotion_evidence_count = promotion_evidence_count
        self._promotion_session_count = promotion_session_count
        self._embedding_provider = embedding_provider
        self._embedding_profile = embedding_profile
        self._recall_experiment_enabled = recall_experiment_enabled
        self._recall_experiment_treatment_percent = (
            recall_experiment_treatment_percent
        )
        self._recall_experiment_salt = recall_experiment_salt
        self._recall_min_evaluated_per_variant = recall_min_evaluated_per_variant
        self._recall_max_adoption_drop = recall_max_adoption_drop
        self._candidate_extractor = candidate_extractor

    def assign_recall_variant(self, oid: int, user_id: int) -> MemoryRecallAssignment:
        """按租户和用户稳定分配召回实验分组。"""

        digest = hashlib.sha256(
            f"{self._recall_experiment_salt}:{oid}:{user_id}".encode()
        ).digest()
        bucket = int.from_bytes(digest[:8], "big") % 10000
        if not self._recall_experiment_enabled:
            variant = MemoryRecallVariant.DISABLED
        elif bucket < self._recall_experiment_treatment_percent * 100:
            variant = MemoryRecallVariant.TREATMENT
        else:
            variant = MemoryRecallVariant.CONTROL
        return MemoryRecallAssignment(recall_variant=variant, bucket=bucket)

    def list_active(
        self,
        oid: int,
        user_id: int,
        *,
        layer: MemoryLayer | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        if limit <= 0 or limit > 100:
            raise MemoryValidationError("MEMORY_LIMIT_INVALID")
        self.expire_stale(oid, user_id)
        return self._repository.list_active(
            oid,
            user_id,
            layer=layer.value if layer is not None else None,
            limit=limit,
        )

    def expire_stale(self, oid: int, user_id: int) -> int:
        """把已过期的 active 记忆转为 expired，并保持用户范围过滤。"""

        now = self._now()
        records = self._repository.list_active(oid, user_id, limit=100)
        expired = [
            item
            for item in records
            if item.expires_at is not None and item.expires_at <= now
        ]
        for item in expired:
            self._repository.update(
                item.model_copy(
                    update={
                        "status": MemoryStatus.EXPIRED,
                        "updated_at": now,
                    }
                )
            )
        if expired:
            self._repository.commit()
        return len(expired)

    def get_owned(self, oid: int, user_id: int, memory_id: int) -> MemoryRecord:
        record = self._repository.get(oid, user_id, memory_id)
        if record is None:
            raise MemoryNotFoundError(memory_id)
        return record

    def build_context(
        self,
        oid: int,
        user_id: int,
        *,
        profile_limit: int = 5,
        scenario_limit: int = 3,
        hint_limit: int = 8,
        query_text: str | None = None,
        intent_type: str | None = None,
        recall_variant: MemoryRecallVariant = MemoryRecallVariant.DISABLED,
    ) -> MemoryContextSnapshot:
        """读取当前用户的分层记忆提示，不读取其他用户或数据集范围。"""

        profile = self.list_active(
            oid,
            user_id,
            layer=MemoryLayer.PROFILE,
            limit=profile_limit,
        )
        scenarios = self.list_active(
            oid,
            user_id,
            layer=MemoryLayer.SCENARIO,
            limit=scenario_limit,
        )
        if intent_type:
            normalized_intent = intent_type.strip().casefold()
            scenarios = [
                item
                for item in scenarios
                if item.payload.get("intent_type") == normalized_intent
            ]
        hints = self.list_active(
            oid,
            user_id,
            layer=MemoryLayer.ATOM,
            limit=100,
        )
        hints = self._rank_hints(
            oid,
            user_id,
            hints,
            query_text=query_text,
            limit=hint_limit,
            recall_variant=recall_variant,
        )

        def item(record: MemoryRecord) -> MemoryContextItem:
            return MemoryContextItem(
                memory_id=record.id,
                layer=record.layer,
                memory_type=record.memory_type,
                statement=record.statement,
                confidence=record.confidence,
                evidence_count=record.evidence_count,
            )

        return MemoryContextSnapshot(
            profile=tuple(item(record) for record in profile),
            scenarios=tuple(item(record) for record in scenarios),
            hints=tuple(item(record) for record in hints),
        )

    def create_manual(
        self,
        oid: int,
        user_id: int,
        payload: MemoryCreateInput,
    ) -> MemoryRecord:
        candidate = MemoryCandidateInput(
            memory_type=payload.memory_type,
            memory_key=payload.memory_key,
            statement=payload.statement,
            payload=payload.payload,
            evidence_type=MemoryEvidenceType.MANUAL_EDIT,
            evidence_text=payload.statement,
            confidence=1.0,
            explicit=True,
        )
        record = self.consolidate_candidate(oid, user_id, candidate)
        if payload.memory_type in {
            MemoryType.PRESENTATION_PREFERENCE,
            MemoryType.NEGATIVE_PREFERENCE,
        }:
            self.rebuild_profile(oid, user_id)
        return record

    def consolidate_candidate(
        self,
        oid: int,
        user_id: int,
        candidate: MemoryCandidateInput,
    ) -> MemoryRecord:
        try:
            key = normalize_memory_key(candidate.memory_key)
            validate_memory_payload(candidate.payload)
        except ValueError as exc:
            raise MemoryValidationError(str(exc)) from exc
        now = self._now()
        existing = self._repository.list_by_key(
            oid,
            user_id,
            candidate.memory_type.value,
            key,
        )
        candidate_fingerprint = payload_fingerprint(candidate.payload)
        matching = next(
            (
                item
                for item in existing
                if payload_fingerprint(item.payload) == candidate_fingerprint
                and item.status != MemoryStatus.DISABLED
            ),
            None,
        )
        if matching is not None:
            status = matching.status
            evidence_count = matching.evidence_count + 1
            session_count = matching.session_count
            if candidate.source_session_id and not self._repository.has_evidence_source_session(
                oid,
                user_id,
                matching.id,
                candidate.source_session_id,
            ):
                session_count += 1
            if candidate.explicit or (
                evidence_count >= self._promotion_evidence_count
                and session_count >= self._promotion_session_count
            ):
                status = MemoryStatus.ACTIVE
            updated = matching.model_copy(
                update={
                    "statement": candidate.statement,
                    "confidence": max(matching.confidence, candidate.confidence),
                    "evidence_count": evidence_count,
                    "session_count": session_count,
                    "status": status,
                    "last_confirmed_at": now,
                    "expires_at": candidate.expires_at or matching.expires_at,
                    "updated_at": now,
                }
            )
            record = self._repository.update(updated)
        else:
            has_active_conflict = any(
                item.status == MemoryStatus.ACTIVE for item in existing
            )
            status = MemoryStatus.ACTIVE if candidate.explicit else MemoryStatus.CANDIDATE
            if has_active_conflict and not candidate.explicit:
                status = MemoryStatus.CONFLICTING
            if candidate.explicit:
                for item in existing:
                    if item.status == MemoryStatus.ACTIVE:
                        self._repository.update(
                            item.model_copy(
                                update={
                                    "status": MemoryStatus.CONFLICTING,
                                    "updated_at": now,
                                }
                            )
                        )
            record = self._repository.create(
                MemoryRecord(
                    id=0,
                    oid=oid,
                    user_id=user_id,
                    layer=candidate.layer,
                    memory_type=candidate.memory_type,
                    memory_key=key,
                    statement=candidate.statement,
                    payload=candidate.payload,
                    confidence=candidate.confidence,
                    evidence_count=1,
                    session_count=1 if candidate.source_session_id else 0,
                    status=status,
                    last_confirmed_at=now if candidate.explicit else None,
                    expires_at=candidate.expires_at
                    or (
                        now + timedelta(days=180)
                        if candidate.evidence_type == MemoryEvidenceType.SUCCESSFUL_QUERY
                        else None
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
        if record.id <= 0:
            raise RuntimeError("MEMORY_ID_MISSING")
        self._repository.create_evidence(
            MemoryEvidenceRecord(
                id=0,
                memory_id=record.id,
                oid=oid,
                user_id=user_id,
                evidence_type=candidate.evidence_type,
                source_ref=candidate.source_ref,
                source_session_id=candidate.source_session_id,
                evidence_text=candidate.evidence_text,
                strength=evidence_strength(candidate.evidence_type),
                created_at=now,
            )
        )
        self._repository.commit()
        self._index_embedding(record)
        return record

    def _index_embedding(self, record: MemoryRecord) -> None:
        """为记忆建立可选向量索引，向量表与主记忆表分离。"""

        provider = self._embedding_provider
        if provider is None:
            return
        if provider.dimension != 1024:
            raise MemoryValidationError("MEMORY_EMBEDDING_DIMENSION_INVALID")
        vector = provider.embed_query(record.statement)
        if len(vector) != 1024:
            raise MemoryValidationError("MEMORY_EMBEDDING_VECTOR_INVALID")
        self._repository.upsert_embedding(
            record.oid,
            record.user_id,
            record.id,
            self._embedding_profile,
            provider.provider,
            provider.model,
            vector,
        )
        self._repository.commit()

    def _rank_hints(
        self,
        oid: int,
        user_id: int,
        hints: list[MemoryRecord],
        *,
        query_text: str | None,
        limit: int,
        recall_variant: MemoryRecallVariant,
    ) -> list[MemoryRecord]:
        if not query_text or not query_text.strip():
            return hints[:limit]
        query = query_text.strip().casefold()
        lexical = sorted(
            hints,
            key=lambda item: _memory_lexical_score(item, query),
            reverse=True,
        )
        lexical = [item for item in lexical if _memory_lexical_score(item, query) > 0]
        dense: list[MemoryRecord] = []
        use_dense = recall_variant in {
            MemoryRecallVariant.DISABLED,
            MemoryRecallVariant.TREATMENT,
        }
        if use_dense and self._embedding_provider is not None:
            provider = self._embedding_provider
            if provider.dimension != 1024:
                raise MemoryValidationError("MEMORY_EMBEDDING_DIMENSION_INVALID")
            vector = provider.embed_query(query_text)
            if len(vector) != 1024:
                raise MemoryValidationError("MEMORY_EMBEDDING_VECTOR_INVALID")
            dense = self._repository.search_dense(
                oid,
                user_id,
                self._embedding_profile,
                vector,
                limit,
            )
        result: list[MemoryRecord] = []
        for item in [*dense, *lexical]:
            if item.id not in {record.id for record in result}:
                result.append(item)
            if len(result) >= limit:
                break
        return result

    def record_clarification(
        self,
        oid: int,
        user_id: int,
        event: ClarificationMemoryEvent,
    ) -> MemoryRecord | None:
        """只从带有长期偏好表达的澄清回答中生成明确候选。"""

        candidates: list[MemoryCandidateInput] = []
        deterministic_candidate = _clarification_candidate(event)
        if deterministic_candidate is not None:
            candidates.append(deterministic_candidate)
        if self._candidate_extractor is not None:
            candidates.extend(self._candidate_extractor.extract(event))
        if not candidates:
            return None
        records = [
            self.consolidate_candidate(oid, user_id, candidate)
            for candidate in candidates
        ]
        if any(
            record.memory_type
            in {
                MemoryType.PRESENTATION_PREFERENCE,
                MemoryType.NEGATIVE_PREFERENCE,
            }
            for record in records
        ):
            self.rebuild_profile(oid, user_id)
        return records[0]

    def rebuild_profile(self, oid: int, user_id: int) -> list[MemoryRecord]:
        """根据有效展示偏好生成确定性的 L3 用户画像。"""

        atoms = self.list_active(oid, user_id, layer=MemoryLayer.ATOM, limit=100)
        sources = [
            item
            for item in atoms
            if item.memory_type
            in {
                MemoryType.PRESENTATION_PREFERENCE,
                MemoryType.NEGATIVE_PREFERENCE,
            }
        ]
        existing_profiles = self.list_active(
            oid,
            user_id,
            layer=MemoryLayer.PROFILE,
            limit=100,
        )
        desired_keys = {
            f"profile:{item.memory_key}" for item in sources
        }
        now = self._now()
        changed = False
        for profile in existing_profiles:
            if profile.memory_key not in desired_keys:
                self._repository.update(
                    profile.model_copy(
                        update={"status": MemoryStatus.DISABLED, "updated_at": now}
                    )
                )
                changed = True

        rebuilt: list[MemoryRecord] = []
        for source in sources:
            profile_key = f"profile:{source.memory_key}"
            payload = {
                "preference": source.payload.get("preference") or source.payload,
                "source_memory_ids": [source.id],
            }
            existing = self._repository.list_by_key(
                oid,
                user_id,
                source.memory_type.value,
                profile_key,
            )
            same = next(
                (
                    item
                    for item in existing
                    if item.status != MemoryStatus.DISABLED
                    and payload_fingerprint(item.payload)
                    == payload_fingerprint(payload)
                ),
                None,
            )
            if same is not None:
                rebuilt.append(same)
                continue
            for item in existing:
                if item.status == MemoryStatus.ACTIVE:
                    self._repository.update(
                        item.model_copy(
                            update={
                                "status": MemoryStatus.CONFLICTING,
                                "updated_at": now,
                            }
                        )
                    )
            version = max((item.version for item in existing), default=0) + 1
            profile = self._repository.create(
                MemoryRecord(
                    id=0,
                    oid=oid,
                    user_id=user_id,
                    layer=MemoryLayer.PROFILE,
                    memory_type=source.memory_type,
                    memory_key=profile_key,
                    statement=source.statement,
                    payload=payload,
                    confidence=source.confidence,
                    evidence_count=source.evidence_count,
                    session_count=source.session_count,
                    version=version,
                    status=MemoryStatus.ACTIVE,
                    last_confirmed_at=source.last_confirmed_at or now,
                    expires_at=source.expires_at,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._repository.create_evidence(
                MemoryEvidenceRecord(
                    id=0,
                    memory_id=profile.id,
                    oid=oid,
                    user_id=user_id,
                    evidence_type=MemoryEvidenceType.REPEATED_BEHAVIOR,
                    source_ref=f"memory:{source.id}",
                    evidence_text="由用户有效偏好重建画像",
                    strength=evidence_strength(MemoryEvidenceType.REPEATED_BEHAVIOR),
                    created_at=now,
                )
            )
            rebuilt.append(profile)
            changed = True
        if changed:
            self._repository.commit()
            for profile in rebuilt:
                if profile.id not in {item.id for item in existing_profiles}:
                    self._index_embedding(profile)
        return rebuilt

    def record_usage(
        self,
        oid: int,
        user_id: int,
        context: MemoryContextSnapshot,
        *,
        stage: str,
        run_id: str | None = None,
        session_id: str | None = None,
        recall_variant: MemoryRecallVariant = MemoryRecallVariant.DISABLED,
    ) -> int:
        """记录已进入 Agent 上下文的记忆，并更新最近使用时间。"""

        if not stage.strip() or len(stage) > 64:
            raise MemoryValidationError("MEMORY_USAGE_STAGE_INVALID")
        try:
            recall_variant = MemoryRecallVariant(recall_variant)
        except (TypeError, ValueError) as exc:
            raise MemoryValidationError("MEMORY_RECALL_VARIANT_INVALID") from exc
        items = [*context.profile, *context.scenarios, *context.hints]
        now = self._now()
        used_ids: set[int] = set()
        for item in items:
            if item.memory_id in used_ids:
                continue
            record = self._repository.get(oid, user_id, item.memory_id)
            if record is None or record.status != MemoryStatus.ACTIVE:
                continue
            self._repository.update(
                record.model_copy(update={"last_used_at": now, "updated_at": now})
            )
            self._repository.create_usage(
                MemoryUsageRecord(
                    id=0,
                    memory_id=record.id,
                    oid=oid,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    stage=stage,
                    matched_by=item.layer.value,
                    recall_variant=recall_variant,
                    adopted=None,
                    created_at=now,
                )
            )
            used_ids.add(item.memory_id)
        if used_ids:
            self._repository.commit()
        return len(used_ids)

    def get_usage_metrics(
        self,
        oid: int,
        user_id: int,
        *,
        limit: int = 100,
    ) -> MemoryUsageMetrics:
        """读取当前用户的记忆使用次数、会话数和采用统计。"""

        if limit <= 0 or limit > 100:
            raise MemoryValidationError("MEMORY_USAGE_METRICS_LIMIT_INVALID")
        self.expire_stale(oid, user_id)
        return self._repository.summarize_usage(oid, user_id, limit=limit)

    def mark_usage_adopted(
        self,
        oid: int,
        user_id: int,
        usage_id: int,
        adopted: bool,
    ) -> MemoryUsageRecord:
        """记录一次记忆使用是否被当前用户或评估流程采用。"""

        if usage_id <= 0:
            raise MemoryValidationError("MEMORY_USAGE_ID_INVALID")
        record = self._repository.mark_usage_adopted(
            oid,
            user_id,
            usage_id,
            adopted,
            self._now(),
        )
        if record is None:
            raise MemoryUsageNotFoundError(usage_id)
        self._repository.commit()
        return record

    def compare_recall_variants(
        self,
        oid: int,
        user_id: int,
        *,
        min_evaluated_count: int | None = None,
        max_adoption_drop: float | None = None,
    ) -> MemoryRecallComparison:
        """比较控制组和实验组，并给出继续收集或回滚建议。"""

        required_count = (
            self._recall_min_evaluated_per_variant
            if min_evaluated_count is None
            else min_evaluated_count
        )
        allowed_drop = (
            self._recall_max_adoption_drop
            if max_adoption_drop is None
            else max_adoption_drop
        )
        if required_count <= 0:
            raise MemoryValidationError("MEMORY_RECALL_MIN_EVALUATED_INVALID")
        if not 0 <= allowed_drop <= 1:
            raise MemoryValidationError("MEMORY_RECALL_MAX_ADOPTION_DROP_INVALID")

        metrics = self.get_usage_metrics(oid, user_id)
        variants = {item.recall_variant: item for item in metrics.by_variant}
        control = variants.get(MemoryRecallVariant.CONTROL)
        treatment = variants.get(MemoryRecallVariant.TREATMENT)
        enough_data = bool(
            control is not None
            and treatment is not None
            and control.evaluated_count >= required_count
            and treatment.evaluated_count >= required_count
        )
        if not enough_data:
            return MemoryRecallComparison(
                control=control,
                treatment=treatment,
                min_evaluated_count=required_count,
                max_adoption_drop=allowed_drop,
                enough_data=False,
                recommendation=MemoryRecallComparisonDecision.COLLECT_MORE_DATA,
            )

        if control.adoption_rate is None or treatment.adoption_rate is None:
            raise MemoryValidationError("MEMORY_RECALL_ADOPTION_RATE_MISSING")
        adoption_rate_delta = treatment.adoption_rate - control.adoption_rate
        recommendation = (
            MemoryRecallComparisonDecision.ROLLBACK_TREATMENT
            if adoption_rate_delta < -allowed_drop
            else MemoryRecallComparisonDecision.CONTINUE_TREATMENT
        )
        return MemoryRecallComparison(
            control=control,
            treatment=treatment,
            min_evaluated_count=required_count,
            max_adoption_drop=allowed_drop,
            enough_data=True,
            adoption_rate_delta=adoption_rate_delta,
            recommendation=recommendation,
        )

    def record_successful_query(
        self,
        oid: int,
        user_id: int,
        event: SuccessfulQueryMemoryEvent,
    ) -> MemoryRecord | None:
        """从成功问数的查询形态生成候选，并在达到阈值后重建场景记忆。"""

        candidate = _query_shape_candidate(event)
        if candidate is None:
            return None
        record = self.consolidate_candidate(oid, user_id, candidate)
        if record.status == MemoryStatus.ACTIVE:
            self.rebuild_scenario(oid, user_id, event.intent_type)
        return record

    def rebuild_scenario(
        self,
        oid: int,
        user_id: int,
        intent_type: str,
    ) -> MemoryRecord | None:
        """根据有效原子记忆生成一个确定性场景摘要。"""

        normalized_intent = intent_type.strip().casefold()
        if not normalized_intent:
            raise MemoryValidationError("MEMORY_SCENARIO_INTENT_EMPTY")
        atoms = self.list_active(
            oid,
            user_id,
            layer=MemoryLayer.ATOM,
            limit=100,
        )
        source = next(
            (
                item
                for item in atoms
                if item.memory_type == MemoryType.QUERY_SHAPE_PREFERENCE
                and item.payload.get("intent_type") == normalized_intent
            ),
            None,
        )
        scenario_key = f"scenario:{normalized_intent}"
        existing = self._repository.list_by_key(
            oid,
            user_id,
            MemoryType.QUERY_SHAPE_PREFERENCE.value,
            scenario_key,
        )
        if source is None:
            for item in existing:
                if item.status == MemoryStatus.ACTIVE:
                    self._repository.update(
                        item.model_copy(
                            update={
                                "status": MemoryStatus.DISABLED,
                                "updated_at": self._now(),
                            }
                        )
                    )
            self._repository.commit()
            return None

        payload = {
            "intent_type": normalized_intent,
            "query_shape": source.payload.get("query_shape") or {},
            "source_memory_ids": [source.id],
        }
        validate_memory_payload(payload)
        statement = _scenario_statement(normalized_intent, payload["query_shape"])
        same = next(
            (
                item
                for item in existing
                if item.status != MemoryStatus.DISABLED
                and payload_fingerprint(item.payload) == payload_fingerprint(payload)
            ),
            None,
        )
        if same is not None:
            return same
        now = self._now()
        for item in existing:
            if item.status == MemoryStatus.ACTIVE:
                self._repository.update(
                    item.model_copy(
                        update={
                            "status": MemoryStatus.CONFLICTING,
                            "updated_at": now,
                        }
                    )
                )
        record = self._repository.create(
            MemoryRecord(
                id=0,
                oid=oid,
                user_id=user_id,
                layer=MemoryLayer.SCENARIO,
                memory_type=MemoryType.QUERY_SHAPE_PREFERENCE,
                memory_key=scenario_key,
                statement=statement,
                payload=payload,
                confidence=source.confidence,
                evidence_count=source.evidence_count,
                session_count=source.session_count,
                status=MemoryStatus.ACTIVE,
                last_confirmed_at=now,
                expires_at=source.expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        self._repository.create_evidence(
            MemoryEvidenceRecord(
                id=0,
                memory_id=record.id,
                oid=oid,
                user_id=user_id,
                evidence_type=MemoryEvidenceType.REPEATED_BEHAVIOR,
                source_ref=f"memory:{source.id}",
                evidence_text="由已达到升级阈值的用户原子记忆生成",
                strength=evidence_strength(MemoryEvidenceType.REPEATED_BEHAVIOR),
                created_at=now,
            )
        )
        self._repository.commit()
        self._index_embedding(record)
        return record

    def update_manual(
        self,
        oid: int,
        user_id: int,
        memory_id: int,
        payload: MemoryUpdateInput,
    ) -> MemoryRecord:
        record = self.get_owned(oid, user_id, memory_id)
        try:
            validate_memory_payload(payload.payload)
        except ValueError as exc:
            raise MemoryValidationError(str(exc)) from exc
        now = self._now()
        updated = record.model_copy(
            update={
                "statement": payload.statement,
                "payload": payload.payload,
                "confidence": 1.0,
                "status": MemoryStatus.ACTIVE,
                "last_confirmed_at": now,
                "updated_at": now,
            }
        )
        result = self._repository.update(updated)
        self._repository.create_evidence(
            MemoryEvidenceRecord(
                id=0,
                memory_id=result.id,
                oid=oid,
                user_id=user_id,
                evidence_type=MemoryEvidenceType.MANUAL_EDIT,
                source_ref=str(memory_id),
                evidence_text=payload.statement,
                strength=1.0,
                created_at=now,
            )
        )
        self._repository.commit()
        self._index_embedding(result)
        if result.layer != MemoryLayer.PROFILE and result.memory_type in {
            MemoryType.PRESENTATION_PREFERENCE,
            MemoryType.NEGATIVE_PREFERENCE,
        }:
            self.rebuild_profile(oid, user_id)
        return result

    def disable(self, oid: int, user_id: int, memory_id: int) -> MemoryRecord:
        record = self.get_owned(oid, user_id, memory_id)
        updated = record.model_copy(
            update={"status": MemoryStatus.DISABLED, "updated_at": self._now()}
        )
        result = self._repository.update(updated)
        self._repository.commit()
        return result

    def delete(self, oid: int, user_id: int, memory_id: int) -> None:
        self.get_owned(oid, user_id, memory_id)
        if not self._repository.delete(oid, user_id, memory_id):
            raise MemoryNotFoundError(memory_id)
        self._repository.commit()


def _query_shape_candidate(
    event: SuccessfulQueryMemoryEvent,
) -> MemoryCandidateInput | None:
    """只提取可解释的查询形态，避免把单次业务查询当成偏好。"""

    intent_type = event.intent_type.strip().casefold()
    query_shape = event.query_shape
    if intent_type in {"trend_analysis", "comparison_analysis"}:
        time_grain = query_shape.get("time_grain")
        if not isinstance(time_grain, str) or not time_grain.strip():
            return None
        shape = {"time_grain": time_grain.strip().casefold()}
        verb = "趋势分析" if intent_type == "trend_analysis" else "对比分析"
        statement = f"用户进行{verb}时倾向按{time_grain}查看"
    elif intent_type == "ranking_analysis":
        limit = query_shape.get("limit")
        if not isinstance(limit, int) or limit <= 0:
            return None
        shape = {"limit": limit}
        statement = f"用户进行排名分析时倾向查看前{limit}项"
    else:
        return None
    return MemoryCandidateInput(
        layer=MemoryLayer.ATOM,
        memory_type=MemoryType.QUERY_SHAPE_PREFERENCE,
        memory_key=f"query_shape:{intent_type}:{next(iter(shape))}",
        statement=statement,
        payload={"intent_type": intent_type, "query_shape": shape},
        evidence_type=MemoryEvidenceType.SUCCESSFUL_QUERY,
        source_ref=event.run_id,
        source_session_id=event.session_id,
        evidence_text=f"成功问数采用{statement[3:]}",
        confidence=0.55,
    )


def _clarification_candidate(
    event: ClarificationMemoryEvent,
) -> MemoryCandidateInput | None:
    """从用户明确表达中提取有限类型的长期偏好。"""

    answer = " ".join(event.answer_text.split())
    persistent_markers = (
        "以后",
        "默认",
        "习惯",
        "通常",
        "每次",
        "都按",
        "不要再",
        "不需要",
    )
    if not any(marker in answer for marker in persistent_markers):
        return None
    evidence_type = (
        MemoryEvidenceType.EXPLICIT_CORRECTION
        if any(marker in answer for marker in ("不对", "不是", "错了", "纠正"))
        else MemoryEvidenceType.EXPLICIT_CONFIRMATION
    )
    if any(marker in answer for marker in ("不要", "不需要")) and any(
        marker in answer for marker in ("图表", "图")
    ):
        return MemoryCandidateInput(
            memory_type=MemoryType.NEGATIVE_PREFERENCE,
            memory_key="presentation:chart",
            statement="用户明确表示默认不需要图表。",
            payload={"preference": "no_chart"},
            evidence_type=evidence_type,
            evidence_text=answer,
            source_ref=event.source_ref,
            source_session_id=event.source_session_id,
            confidence=0.95,
            explicit=True,
        )
    if "先" in answer and any(marker in answer for marker in ("总数", "总体", "结论")):
        return MemoryCandidateInput(
            memory_type=MemoryType.PRESENTATION_PREFERENCE,
            memory_key="presentation:answer_order",
            statement="用户明确偏好先展示总体结论，再展示明细。",
            payload={"preference": "summary_first"},
            evidence_type=evidence_type,
            evidence_text=answer,
            source_ref=event.source_ref,
            source_session_id=event.source_session_id,
            confidence=0.95,
            explicit=True,
        )
    grain_aliases = {
        "天": "day",
        "日": "day",
        "周": "week",
        "月": "month",
        "季度": "quarter",
        "年": "year",
    }
    for alias, grain in grain_aliases.items():
        if f"按{alias}" in answer or f"每{alias}" in answer:
            return MemoryCandidateInput(
                memory_type=MemoryType.QUERY_SHAPE_PREFERENCE,
                memory_key="query_shape:time_grain",
                statement=f"用户明确偏好按{alias}查看趋势。",
                payload={"preference": grain},
                evidence_type=evidence_type,
                evidence_text=answer,
                source_ref=event.source_ref,
                source_session_id=event.source_session_id,
                confidence=0.95,
                explicit=True,
            )
    question_key = normalize_memory_key(f"clarification:{event.question}")
    return MemoryCandidateInput(
        memory_type=MemoryType.CORRECTION,
        memory_key=question_key,
        statement=f"用户明确说明：{answer}",
        payload={"preference": answer[:240]},
        evidence_type=evidence_type,
        evidence_text=answer,
        source_ref=event.source_ref,
        source_session_id=event.source_session_id,
        confidence=0.9,
        explicit=True,
    )


def _scenario_statement(intent_type: str, query_shape: dict[str, object]) -> str:
    """按结构化原子记忆生成场景摘要，不调用模型。"""

    if "time_grain" in query_shape:
        verb = "趋势分析" if intent_type == "trend_analysis" else "对比分析"
        return f"用户进行{verb}时倾向按{query_shape['time_grain']}查看。"
    if "limit" in query_shape:
        return f"用户进行排名分析时倾向查看前{query_shape['limit']}项。"
    return f"用户进行{intent_type}时有稳定的查询形态偏好。"


def _memory_lexical_score(record: MemoryRecord, query: str) -> int:
    """按记忆键、结构化查询形态和摘要文本计算轻量词法分数。"""

    score = 0
    if record.memory_key.casefold() in query:
        score += 5
    statement = record.statement.casefold()
    if statement in query or query in statement:
        score += 4
    query_shape = record.payload.get("query_shape")
    if isinstance(query_shape, dict):
        time_grain = str(query_shape.get("time_grain") or "").casefold()
        aliases = {
            "day": ("按天", "按日", "每天"),
            "week": ("按周", "每周"),
            "month": ("按月", "每月"),
            "quarter": ("按季度", "每季度"),
            "year": ("按年", "每年"),
        }
        if time_grain and any(alias in query for alias in aliases.get(time_grain, ())):
            score += 4
        limit = query_shape.get("limit")
        if isinstance(limit, int) and str(limit) in query:
            score += 3
    for index in range(max(len(query) - 1, 0)):
        if query[index : index + 2] in statement:
            score += 1
    return score


__all__ = ["MemoryService"]
