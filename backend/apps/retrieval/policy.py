"""semantic_binding 的候选重排、槽位门控与统一结果组装。"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval.errors import (
    RetrievalProviderUnavailableError,
    RetrievalQueryError,
)
from apps.retrieval.hybrid import HybridRecallResult, SubQueryRecallResult
from apps.retrieval.profiles import (
    SemanticBindingGateThreshold,
    get_retrieval_profile,
)
from apps.retrieval.schemas import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalAmbiguity,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalProfileName,
    RetrievalResourceType,
    RetrievalSlotDecision,
)


class RerankCandidate(BaseModel):
    """传给外部 reranker 的封闭候选，ID 由检索层生成。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    snippet: str = ""


class RerankScore(BaseModel):
    """reranker 只能为已有候选返回可校准分数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)


class CandidateReranker(Protocol):
    """可替换的 cross-encoder/reranker 端口。"""

    provider: str
    model: str

    def rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> list[RerankScore]:
        """只为传入候选评分，不得生成新候选。"""


class SemanticBindingPolicyResult(BaseModel):
    """保留召回事实和最终 Bundle，供诊断与离线评测使用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recall: HybridRecallResult
    bundle: RetrievalBundle


class _SlotPolicyResult(BaseModel):
    """policy 内部的单槽结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: SubQueryRecallResult
    hits: tuple[RetrievalHit, ...]
    decision: RetrievalSlotDecision
    ambiguity: RetrievalAmbiguity | None = None
    rerank_diagnostic: RetrievalChannelDiagnostic


class _CandidateEvidence(BaseModel):
    """门控只在同一证据量纲内比较绝对值与 gap。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    score: float


class SemanticBindingPolicy:
    """对语义绑定召回执行版本化、确定性的业务决策。"""

    def __init__(self, reranker: CandidateReranker | None = None) -> None:
        self._reranker = reranker

    def apply(self, recall: HybridRecallResult) -> SemanticBindingPolicyResult:
        started = perf_counter()
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        policy_config = profile.semantic_binding_policy
        if policy_config is None:
            raise RetrievalQueryError("SEMANTIC_BINDING_POLICY_NOT_CONFIGURED")

        slot_results = [
            self._apply_slot(
                slot, policy_config.thresholds[_resource_type_for_slot(slot)]
            )
            for slot in recall.slots
        ]
        decisions = [item.decision for item in slot_results]
        ambiguities = [
            item.ambiguity for item in slot_results if item.ambiguity is not None
        ]
        degraded_codes = _degraded_reason_codes(slot_results)
        decision_status = self._overall_status(slot_results, degraded_codes)
        reason_codes = _overall_reason_codes(decision_status, decisions, degraded_codes)
        allowed_assets = _allowed_executable_assets(decisions)

        decision = RetrievalDecision(
            status=decision_status,
            slot_decisions=decisions,
            ambiguities=ambiguities,
            allowed_asset_ids=allowed_assets,
            reason_codes=reason_codes,
        )
        bundle = RetrievalBundle(
            request_id=recall.request_id,
            bindings=_bindings_from_slots(slot_results),
            decision=decision,
            diagnostics=RetrievalDiagnostics(
                strategy_version=f"{profile.version}:{policy_config.version}",
                index_generation=",".join(recall.index_generations) or "not-observed",
                channels=_aggregate_channel_diagnostics(slot_results),
                total_latency_ms=recall.total_latency_ms
                + (perf_counter() - started) * 1000,
                degraded_reason=",".join(degraded_codes) if degraded_codes else None,
            ),
        )
        return SemanticBindingPolicyResult(recall=recall, bundle=bundle)

    def _apply_slot(
        self,
        slot: SubQueryRecallResult,
        threshold: SemanticBindingGateThreshold,
    ) -> _SlotPolicyResult:
        hits, rerank_diagnostic = self._rerank(slot)
        candidate_assets = _unique_asset_refs(hits)
        referenced_hits = [hit for hit in hits if hit.asset_ref is not None]
        if not referenced_hits:
            reason = (
                "NO_CANDIDATE_ABOVE_HARD_FILTERS"
                if not hits
                else "CANDIDATE_ASSET_REFERENCE_MISSING"
            )
            return _SlotPolicyResult(
                slot=slot,
                hits=hits,
                decision=RetrievalSlotDecision(
                    subquery_id=slot.subquery.subquery_id,
                    purpose=slot.subquery.purpose,
                    status=RetrievalDecisionStatus.MISSED,
                    candidate_assets=candidate_assets,
                    reason_codes=[reason],
                ),
                rerank_diagnostic=rerank_diagnostic,
            )

        top = referenced_hits[0]
        top_evidence = _candidate_evidence(top, threshold)
        if top_evidence is None:
            return _SlotPolicyResult(
                slot=slot,
                hits=hits,
                decision=RetrievalSlotDecision(
                    subquery_id=slot.subquery.subquery_id,
                    purpose=slot.subquery.purpose,
                    status=RetrievalDecisionStatus.MISSED,
                    candidate_assets=candidate_assets,
                    reason_codes=["TOP1_BELOW_ABSOLUTE_THRESHOLD"],
                ),
                rerank_diagnostic=rerank_diagnostic,
            )

        second = referenced_hits[1] if len(referenced_hits) > 1 else None
        ambiguity_reason = _ambiguity_reason(top_evidence, second, threshold)
        if ambiguity_reason:
            return _SlotPolicyResult(
                slot=slot,
                hits=hits,
                decision=RetrievalSlotDecision(
                    subquery_id=slot.subquery.subquery_id,
                    purpose=slot.subquery.purpose,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=candidate_assets,
                    reason_codes=[ambiguity_reason],
                ),
                ambiguity=RetrievalAmbiguity(
                    subquery_id=slot.subquery.subquery_id,
                    reason_code=ambiguity_reason,
                    candidate_assets=candidate_assets,
                ),
                rerank_diagnostic=rerank_diagnostic,
            )

        assert top.asset_ref is not None
        return _SlotPolicyResult(
            slot=slot,
            hits=hits,
            decision=RetrievalSlotDecision(
                subquery_id=slot.subquery.subquery_id,
                purpose=slot.subquery.purpose,
                status=RetrievalDecisionStatus.RESOLVED,
                candidate_assets=candidate_assets,
                selected_assets=[top.asset_ref],
                reason_codes=[f"TOP1_ACCEPTED_BY_{top_evidence.kind.upper()}"],
            ),
            rerank_diagnostic=rerank_diagnostic,
        )

    def _rerank(
        self,
        slot: SubQueryRecallResult,
    ) -> tuple[tuple[RetrievalHit, ...], RetrievalChannelDiagnostic]:
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        candidates = slot.hits[: profile.rerank_limit]
        if self._reranker is None or slot.fast_path or not candidates:
            return tuple(
                sorted(slot.hits, key=_deterministic_hit_sort_key)
            ), _skipped_rerank_diagnostic()

        started = perf_counter()
        rerank_input = tuple(
            RerankCandidate(
                candidate_id=hit.resource_id,
                title=hit.title,
                snippet=hit.snippet,
            )
            for hit in candidates
        )
        try:
            scores = self._reranker.rerank(slot.subquery.text, rerank_input)
        except RetrievalProviderUnavailableError as exc:
            return tuple(
                sorted(slot.hits, key=_deterministic_hit_sort_key)
            ), RetrievalChannelDiagnostic(
                channel=RetrievalChannel.RERANK,
                status=RetrievalChannelStatus.UNAVAILABLE,
                latency_ms=(perf_counter() - started) * 1000,
                error_code=str(exc.details.get("reason_code") or exc.code),
            )

        score_by_id = _validated_rerank_scores(rerank_input, scores)
        reranked = tuple(
            hit.model_copy(
                update={
                    "scores": hit.scores.model_copy(
                        update={"rerank": score_by_id.get(hit.resource_id)}
                    )
                }
            )
            for hit in slot.hits
        )
        return tuple(
            sorted(reranked, key=_deterministic_hit_sort_key)
        ), RetrievalChannelDiagnostic(
            channel=RetrievalChannel.RERANK,
            status=RetrievalChannelStatus.SUCCEEDED,
            latency_ms=(perf_counter() - started) * 1000,
            candidate_count=len(score_by_id),
        )

    @staticmethod
    def _overall_status(
        slot_results: list[_SlotPolicyResult],
        degraded_codes: list[str],
    ) -> RetrievalDecisionStatus:
        required = [item for item in slot_results if item.slot.subquery.required]
        resolved = [
            item
            for item in required
            if item.decision.status == RetrievalDecisionStatus.RESOLVED
        ]
        if required and len(resolved) == len(required) and _is_cross_model(resolved):
            return RetrievalDecisionStatus.CROSS_MODEL
        if degraded_codes:
            return RetrievalDecisionStatus.DEGRADED
        if any(
            item.decision.status == RetrievalDecisionStatus.AMBIGUOUS
            for item in required
        ):
            return RetrievalDecisionStatus.AMBIGUOUS
        if not required or not resolved:
            return RetrievalDecisionStatus.MISSED
        if len(resolved) != len(required):
            return RetrievalDecisionStatus.PARTIAL
        return RetrievalDecisionStatus.RESOLVED


def _validated_rerank_scores(
    candidates: tuple[RerankCandidate, ...],
    scores: list[RerankScore],
) -> dict[str, float]:
    candidate_ids = {item.candidate_id for item in candidates}
    returned_ids = [item.candidate_id for item in scores]
    if len(returned_ids) != len(set(returned_ids)):
        raise RetrievalQueryError(
            "RERANKER_RETURNED_DUPLICATE_CANDIDATE",
            details={"reason_code": "RERANKER_DUPLICATE_CANDIDATE_ID"},
        )
    unexpected = sorted(set(returned_ids) - candidate_ids)
    if unexpected:
        raise RetrievalQueryError(
            "RERANKER_RETURNED_UNKNOWN_CANDIDATE",
            details={
                "reason_code": "RERANKER_CANDIDATE_SET_VIOLATION",
                "unexpected_candidate_ids": unexpected,
            },
        )
    return {item.candidate_id: item.score for item in scores}


def _deterministic_hit_sort_key(hit: RetrievalHit) -> tuple[Any, ...]:
    # 精确身份事实优先于模型分数；其余候选由同量纲 rerank 或 RRF 稳定排序。
    if hit.scores.exact is not None:
        evidence_tier = 0
    elif hit.scores.alias is not None:
        evidence_tier = 1
    elif hit.scores.rerank is not None:
        evidence_tier = 2
    else:
        evidence_tier = 3
    support_count = sum(
        score is not None
        for score in (
            hit.scores.exact,
            hit.scores.alias,
            hit.scores.lexical,
            hit.scores.dense,
        )
    )
    return (
        evidence_tier,
        -(hit.scores.rerank if hit.scores.rerank is not None else -1),
        -support_count,
        -(hit.scores.final or 0),
        hit.resource_id,
    )


def _candidate_evidence(
    hit: RetrievalHit,
    threshold: SemanticBindingGateThreshold,
) -> _CandidateEvidence | None:
    if hit.scores.exact is not None:
        return _CandidateEvidence(kind="exact", score=hit.scores.exact)
    if hit.scores.alias is not None:
        return _CandidateEvidence(kind="alias", score=hit.scores.alias)
    if (
        hit.scores.rerank is not None
        and hit.scores.rerank >= threshold.min_rerank_score
    ):
        return _CandidateEvidence(kind="rerank", score=hit.scores.rerank)

    eligible: list[_CandidateEvidence] = []
    if (
        hit.scores.lexical is not None
        and hit.scores.lexical >= threshold.min_lexical_score
    ):
        eligible.append(_CandidateEvidence(kind="lexical", score=hit.scores.lexical))
    if hit.scores.dense is not None and hit.scores.dense >= threshold.min_dense_score:
        eligible.append(_CandidateEvidence(kind="dense", score=hit.scores.dense))
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item.score, item.kind))


def _ambiguity_reason(
    top_evidence: _CandidateEvidence,
    second: RetrievalHit | None,
    threshold: SemanticBindingGateThreshold,
) -> str | None:
    if second is None:
        return None
    second_evidence = _candidate_evidence(second, threshold)
    if second_evidence is None:
        return None
    identity_kinds = {"exact", "alias"}
    if top_evidence.kind in identity_kinds:
        return (
            "MULTIPLE_IDENTITY_MATCHES"
            if second_evidence.kind in identity_kinds
            else None
        )
    if top_evidence.kind != second_evidence.kind:
        return "TOP_CANDIDATES_USE_INCOMPARABLE_SCORE_CHANNELS"
    if top_evidence.score - second_evidence.score < threshold.min_top_gap:
        return "TOP1_TOP2_GAP_BELOW_THRESHOLD"
    return None


def _resource_type_for_slot(slot: SubQueryRecallResult) -> RetrievalResourceType:
    mapping = {
        "metric": RetrievalResourceType.METRIC,
        "dimension": RetrievalResourceType.DIMENSION,
        "value": RetrievalResourceType.VALUE,
        "term": RetrievalResourceType.TERM,
    }
    try:
        return mapping[slot.subquery.purpose.value]
    except KeyError as exc:
        raise RetrievalQueryError(
            f"SEMANTIC_BINDING_POLICY_PURPOSE_UNSUPPORTED:{slot.subquery.purpose.value}"
        ) from exc


def _is_cross_model(resolved: list[_SlotPolicyResult]) -> bool:
    selected_hits = [
        hit
        for item in resolved
        for hit in item.hits
        if hit.asset_ref is not None and hit.asset_ref in item.decision.selected_assets
    ]
    metric_hits = [
        hit
        for hit in selected_hits
        if hit.resource_type == RetrievalResourceType.METRIC
    ]
    metric_model_ids = {
        hit.asset_ref.model_id
        for hit in metric_hits
        if hit.asset_ref is not None and hit.asset_ref.model_id is not None
    }
    if len(metric_model_ids) > 1:
        return True
    if not metric_model_ids:
        executable_models = {
            hit.asset_ref.model_id
            for hit in selected_hits
            if hit.resource_type
            in {RetrievalResourceType.METRIC, RetrievalResourceType.DIMENSION}
            and hit.asset_ref is not None
            and hit.asset_ref.model_id is not None
        }
        return len(executable_models) > 1

    metric_model_id = next(iter(metric_model_ids))
    compatible_dimension_ids_by_metric = [
        {
            int(value)
            for value in hit.metadata.get("compatible_dimension_ids", [])
            if isinstance(value, int) and value > 0
        }
        for hit in metric_hits
    ]
    for hit in selected_hits:
        if hit.resource_type not in {
            RetrievalResourceType.DIMENSION,
            RetrievalResourceType.VALUE,
        }:
            continue
        assert hit.asset_ref is not None
        if hit.asset_ref.model_id == metric_model_id:
            continue
        if not all(
            hit.asset_ref.asset_id in compatible_ids
            for compatible_ids in compatible_dimension_ids_by_metric
        ):
            return True
    return False


def _allowed_executable_assets(
    decisions: list[RetrievalSlotDecision],
) -> list[ExecutableAssetReference]:
    allowed: list[ExecutableAssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for decision in decisions:
        for asset in decision.selected_assets:
            executable_type: Literal[
                RetrievalResourceType.METRIC,
                RetrievalResourceType.DIMENSION,
            ]
            if asset.asset_type == RetrievalResourceType.METRIC:
                executable_type = RetrievalResourceType.METRIC
            elif asset.asset_type == RetrievalResourceType.DIMENSION:
                executable_type = RetrievalResourceType.DIMENSION
            else:
                continue
            key = (executable_type.value, asset.asset_id, asset.model_id)
            if key in seen:
                continue
            seen.add(key)
            allowed.append(
                ExecutableAssetReference(
                    asset_type=executable_type,
                    asset_id=asset.asset_id,
                    model_id=asset.model_id,
                )
            )
    return allowed


def _unique_asset_refs(hits: tuple[RetrievalHit, ...]) -> list[AssetReference]:
    assets: list[AssetReference] = []
    seen: set[tuple[str, int, int | None]] = set()
    for hit in hits:
        if hit.asset_ref is None:
            continue
        key = (
            hit.asset_ref.asset_type.value,
            hit.asset_ref.asset_id,
            hit.asset_ref.model_id,
        )
        if key in seen:
            continue
        seen.add(key)
        assets.append(hit.asset_ref)
    return assets


def _bindings_from_slots(slot_results: list[_SlotPolicyResult]) -> RetrievalBindings:
    grouped: dict[RetrievalResourceType, list[RetrievalHit]] = {}
    seen: set[str] = set()
    for slot_result in slot_results:
        for hit in slot_result.hits:
            if hit.resource_id in seen:
                continue
            seen.add(hit.resource_id)
            grouped.setdefault(hit.resource_type, []).append(hit)
    return RetrievalBindings(
        metrics=grouped.get(RetrievalResourceType.METRIC, []),
        dimensions=grouped.get(RetrievalResourceType.DIMENSION, []),
        values=grouped.get(RetrievalResourceType.VALUE, []),
        terms=grouped.get(RetrievalResourceType.TERM, []),
    )


def _aggregate_channel_diagnostics(
    slot_results: list[_SlotPolicyResult],
) -> list[RetrievalChannelDiagnostic]:
    grouped: dict[RetrievalChannel, list[RetrievalChannelDiagnostic]] = {}
    for slot_result in slot_results:
        for diagnostic in (*slot_result.slot.channels, slot_result.rerank_diagnostic):
            grouped.setdefault(diagnostic.channel, []).append(diagnostic)

    status_priority = {
        RetrievalChannelStatus.FAILED: 3,
        RetrievalChannelStatus.UNAVAILABLE: 2,
        RetrievalChannelStatus.SUCCEEDED: 1,
        RetrievalChannelStatus.SKIPPED: 0,
    }
    diagnostics: list[RetrievalChannelDiagnostic] = []
    for channel in RetrievalChannel:
        items = grouped.get(channel)
        if not items:
            continue
        status = max((item.status for item in items), key=status_priority.__getitem__)
        error_codes = sorted({item.error_code for item in items if item.error_code})
        diagnostics.append(
            RetrievalChannelDiagnostic(
                channel=channel,
                status=status,
                latency_ms=sum(item.latency_ms for item in items),
                candidate_count=sum(item.candidate_count for item in items),
                error_code=",".join(error_codes) if error_codes else None,
            )
        )
    return diagnostics


def _degraded_reason_codes(slot_results: list[_SlotPolicyResult]) -> list[str]:
    codes = {
        diagnostic.error_code
        for slot_result in slot_results
        for diagnostic in (*slot_result.slot.channels, slot_result.rerank_diagnostic)
        if diagnostic.status
        in {RetrievalChannelStatus.UNAVAILABLE, RetrievalChannelStatus.FAILED}
        and diagnostic.error_code
    }
    return sorted(codes)


def _overall_reason_codes(
    status: RetrievalDecisionStatus,
    decisions: list[RetrievalSlotDecision],
    degraded_codes: list[str],
) -> list[str]:
    codes = [f"SEMANTIC_BINDING_{status.value.upper()}"]
    codes.extend(degraded_codes)
    codes.extend(code for decision in decisions for code in decision.reason_codes)
    return list(dict.fromkeys(codes))


def _skipped_rerank_diagnostic() -> RetrievalChannelDiagnostic:
    return RetrievalChannelDiagnostic(
        channel=RetrievalChannel.RERANK,
        status=RetrievalChannelStatus.SKIPPED,
    )


__all__ = [
    "CandidateReranker",
    "RerankCandidate",
    "RerankScore",
    "SemanticBindingPolicy",
    "SemanticBindingPolicyResult",
]
