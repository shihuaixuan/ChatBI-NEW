"""semantic_binding 的候选重排、槽位门控与统一结果组装。"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval.errors import (
    RetrievalProviderUnavailableError,
    RetrievalQueryError,
)
from apps.retrieval.models.dto import (
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
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScores,
    RetrievalSlotDecision,
    RetrievalSourceType,
)
from apps.retrieval.query.decision import (
    compatible_dimension_selections,
    prefer_dimension_assets,
    selection_requires_cross_model,
)
from apps.retrieval.query.hybrid import HybridRecallResult, SubQueryRecallResult
from apps.retrieval.query.profiles import (
    SemanticBindingGateThreshold,
    get_retrieval_profile,
)
from apps.semantic.models.dto import DatasetSchema, SchemaElement


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
    eligible_assets: list[AssetReference] = Field(default_factory=list)
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
        slot_results = [
            _collapse_value_ambiguity_to_single_dimension(
                item,
                threshold=policy_config.thresholds[RetrievalResourceType.VALUE],
            )
            for item in slot_results
        ]
        slot_results = _constrain_metric_dimension_candidates(slot_results)
        slot_results = _resolve_identity_dimensions_by_metric_compatibility(
            slot_results
        )
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
        eligible_assets = _unique_asset_refs(
            tuple(
                hit
                for hit in referenced_hits
                if _candidate_evidence(hit, threshold) is not None
            )
        )
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
                eligible_assets=eligible_assets,
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
                eligible_assets=eligible_assets,
                rerank_diagnostic=rerank_diagnostic,
            )

        second = referenced_hits[1] if len(referenced_hits) > 1 else None
        ambiguity_reason = _ambiguity_reason(top_evidence, second, threshold)
        if ambiguity_reason:
            # 澄清选项只包含达到当前证据门槛的资产，避免把召回 TopK 全量暴露给用户。
            clarification_assets = _unique_asset_refs(
                tuple(
                    hit
                    for hit in referenced_hits
                    if _candidate_evidence(hit, threshold) is not None
                )
            )
            return _SlotPolicyResult(
                slot=slot,
                hits=hits,
                decision=RetrievalSlotDecision(
                    subquery_id=slot.subquery.subquery_id,
                    purpose=slot.subquery.purpose,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=clarification_assets,
                    reason_codes=[ambiguity_reason],
                ),
                ambiguity=RetrievalAmbiguity(
                    subquery_id=slot.subquery.subquery_id,
                    reason_code=ambiguity_reason,
                    candidate_assets=clarification_assets,
                ),
                eligible_assets=eligible_assets,
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
            eligible_assets=eligible_assets,
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
        # VALUE 槽是辅助值归一查询：未命中按数据集策略保留原值即可，
        # 但多命中的取值歧义必须交给用户消解，不能静默取 Top1。
        ambiguity_slots = [
            item
            for item in slot_results
            if item.decision.status == RetrievalDecisionStatus.AMBIGUOUS
            and (
                item.slot.subquery.required
                or item.slot.subquery.purpose == RetrievalPurpose.VALUE
            )
        ]
        if ambiguity_slots:
            return RetrievalDecisionStatus.AMBIGUOUS
        if not required or not resolved:
            return RetrievalDecisionStatus.MISSED
        if len(resolved) != len(required):
            return RetrievalDecisionStatus.PARTIAL
        if degraded_codes:
            # 通道降级只描述已完整收敛决策的质量，不能覆盖歧义、缺失或部分命中。
            return RetrievalDecisionStatus.DEGRADED
        return RetrievalDecisionStatus.RESOLVED


def _collapse_value_ambiguity_to_single_dimension(
    item: _SlotPolicyResult,
    *,
    threshold: SemanticBindingGateThreshold,
) -> _SlotPolicyResult:
    """同维度的多值命中不是资产歧义：维度唯一即确定性收敛，canonical 不确定时保留原值。

    VALUE 候选的 asset_id 指向所属维度；多个候选若同属一个维度，
    澄清选项无法区分具体维值，只能按确定性顺序取 Top1 并标注收敛原因。
    """

    if (
        item.slot.subquery.purpose != RetrievalPurpose.VALUE
        or item.decision.status != RetrievalDecisionStatus.AMBIGUOUS
    ):
        return item
    eligible = _unique_asset_refs(
        tuple(
            hit
            for hit in item.hits
            if hit.asset_ref is not None
            and _candidate_evidence(hit, threshold) is not None
        )
    )
    if len(eligible) != 1:
        return item
    return _SlotPolicyResult(
        slot=item.slot,
        hits=item.hits,
        decision=RetrievalSlotDecision(
            subquery_id=item.decision.subquery_id,
            purpose=item.decision.purpose,
            status=RetrievalDecisionStatus.RESOLVED,
            candidate_assets=item.decision.candidate_assets,
            selected_assets=eligible,
            reason_codes=["VALUE_AMBIGUITY_COLLAPSED_TO_SINGLE_DIMENSION"],
        ),
        eligible_assets=item.eligible_assets,
        rerank_diagnostic=item.rerank_diagnostic,
    )


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
    if hit.scores.rerank is not None:
        # rerank 已执行时以其结果为准，低分候选不能再用召回阶段分数绕过门槛。
        if hit.scores.rerank >= threshold.min_rerank_score:
            return _CandidateEvidence(kind="rerank", score=hit.scores.rerank)
        return None

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
    selected_assets = [
        hit.asset_ref for hit in selected_hits if hit.asset_ref is not None
    ]
    return selection_requires_cross_model(selected_hits, selected_assets)


def _resolve_identity_dimensions_by_metric_compatibility(
    slot_results: list[_SlotPolicyResult],
) -> list[_SlotPolicyResult]:
    """同义维度身份命中不唯一时，用已选指标的可执行模型关系确定唯一资产。"""

    selections = compatible_dimension_selections(
        (hit for item in slot_results for hit in item.hits),
        [item.decision for item in slot_results],
    )
    if not selections:
        return slot_results

    resolved_results: list[_SlotPolicyResult] = []
    for item in slot_results:
        selected = selections.get(item.decision.subquery_id)
        if selected is None:
            resolved_results.append(item)
            continue
        resolved_results.append(
            item.model_copy(
                update={
                    "decision": item.decision.model_copy(
                        update={
                            "status": RetrievalDecisionStatus.RESOLVED,
                            "selected_assets": [selected],
                            "reason_codes": [
                                "IDENTITY_DISAMBIGUATED_BY_METRIC_MODEL_COMPATIBILITY"
                            ],
                        }
                    ),
                    "ambiguity": None,
                }
            )
        )
    return resolved_results


def _constrain_metric_dimension_candidates(
    slot_results: list[_SlotPolicyResult],
) -> list[_SlotPolicyResult]:
    """先按模型关系删除不兼容候选，再决定自动绑定或联合澄清。"""

    metric_results = [
        item
        for item in slot_results
        if item.decision.purpose == RetrievalPurpose.METRIC
        and item.decision.status
        in {RetrievalDecisionStatus.RESOLVED, RetrievalDecisionStatus.AMBIGUOUS}
    ]
    dimension_results = [
        item
        for item in slot_results
        if item.decision.purpose == RetrievalPurpose.DIMENSION
        and item.decision.status
        in {RetrievalDecisionStatus.RESOLVED, RetrievalDecisionStatus.AMBIGUOUS}
        and (
            item.decision.status == RetrievalDecisionStatus.RESOLVED
            or all(
                (
                    hit := next(
                        (
                            candidate_hit
                            for candidate_hit in item.hits
                            if candidate_hit.asset_ref == asset
                        ),
                        None,
                    )
                )
                is not None
                and (hit.scores.exact is not None or hit.scores.alias is not None)
                for asset in item.decision.candidate_assets
            )
        )
    ]
    if not metric_results or not dimension_results:
        return slot_results

    hits_by_asset = {
        _reference_key(hit.asset_ref): hit
        for item in slot_results
        for hit in item.hits
        if hit.asset_ref is not None
    }
    active: dict[str, list[AssetReference]] = {}
    for item in [*metric_results, *dimension_results]:
        if item.decision.purpose == RetrievalPurpose.DIMENSION:
            candidates = item.eligible_assets or item.decision.candidate_assets
        elif item.decision.status == RetrievalDecisionStatus.RESOLVED:
            candidates = item.decision.selected_assets
        else:
            candidates = item.eligible_assets or item.decision.candidate_assets
        active[item.decision.subquery_id] = list(candidates)

    metric_model_ids = {
        asset.model_id
        for item in metric_results
        for asset in active[item.decision.subquery_id]
        if asset.model_id is not None
    }
    if len(metric_model_ids) > 1:
        # 跨模型查询由后续 payload 按指标模型拆分；不能先用“所有指标
        # 同时兼容所有维度”的单模型规则把候选清空。
        return slot_results

    changed = True
    while changed:
        changed = False
        for metric_item in metric_results:
            metric_id = metric_item.decision.subquery_id
            compatible_metrics = [
                metric
                for metric in active[metric_id]
                if all(
                    any(
                        _metric_dimension_assets_compatible(
                            metric,
                            dimension,
                            hits_by_asset,
                        )
                        for dimension in active[dimension_item.decision.subquery_id]
                    )
                    for dimension_item in dimension_results
                )
            ]
            if len(compatible_metrics) != len(active[metric_id]):
                active[metric_id] = compatible_metrics
                changed = True

        for dimension_item in dimension_results:
            dimension_id = dimension_item.decision.subquery_id
            compatible_dimensions = [
                dimension
                for dimension in active[dimension_id]
                if all(
                    any(
                        _metric_dimension_assets_compatible(
                            metric,
                            dimension,
                            hits_by_asset,
                        )
                        for metric in active[metric_item.decision.subquery_id]
                    )
                    for metric_item in metric_results
                )
            ]
            if len(compatible_dimensions) != len(active[dimension_id]):
                active[dimension_id] = compatible_dimensions
                changed = True

    empty_slots = sorted(
        subquery_id for subquery_id, candidates in active.items() if not candidates
    )
    if empty_slots:
        raise RetrievalQueryError(
            "检索到的指标与维度不存在可执行的语义模型组合",
            details={
                "reason_code": "SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE",
                "incompatible_subquery_ids": empty_slots,
            },
        )

    metric_scope_assets = _metric_scope_assets(metric_results, active)
    all_hits = tuple(hit for item in slot_results for hit in item.hits)
    constrained: list[_SlotPolicyResult] = []
    for item in slot_results:
        candidates = active.get(item.decision.subquery_id)
        if candidates is None:
            constrained.append(item)
            continue
        was_resolved = item.decision.status == RetrievalDecisionStatus.RESOLVED
        if item.decision.purpose == RetrievalPurpose.DIMENSION:
            candidates = prefer_dimension_assets(
                all_hits,
                metric_scope_assets,
                candidates,
            )
            if not candidates:
                raise RetrievalQueryError(
                    "检索到的指标与维度不存在可执行的语义模型组合",
                    details={
                        "reason_code": "SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE",
                        "incompatible_subquery_ids": [
                            item.decision.subquery_id
                        ],
                    },
                )
        if len(candidates) == 1 or was_resolved:
            selected = candidates[0]
            if was_resolved and item.decision.selected_assets:
                selected = next(
                    (
                        asset
                        for asset in item.decision.selected_assets
                        if asset in candidates
                    ),
                    selected,
                )
            reason_code = (
                "IDENTITY_DISAMBIGUATED_BY_METRIC_MODEL_COMPATIBILITY"
                if item.decision.purpose == RetrievalPurpose.DIMENSION
                else "CANDIDATE_RESOLVED_BY_METRIC_DIMENSION_COMPATIBILITY"
            )
            constrained.append(
                item.model_copy(
                    update={
                        "decision": item.decision.model_copy(
                            update={
                                "candidate_assets": candidates,
                                "status": RetrievalDecisionStatus.RESOLVED,
                                "selected_assets": [selected],
                                "reason_codes": [reason_code],
                            }
                        ),
                        "ambiguity": None,
                    }
                )
            )
            continue
        ambiguity = item.ambiguity
        if ambiguity is None and item.decision.purpose == RetrievalPurpose.DIMENSION:
            ambiguity = RetrievalAmbiguity(
                subquery_id=item.decision.subquery_id,
                reason_code="MULTIPLE_COMPATIBLE_DIMENSIONS",
                candidate_assets=candidates,
            )
        constrained.append(
            item.model_copy(
                update={
                    "decision": item.decision.model_copy(
                        update={
                            "status": RetrievalDecisionStatus.AMBIGUOUS,
                            "candidate_assets": candidates,
                            "selected_assets": [],
                        }
                    ),
                    "ambiguity": ambiguity.model_copy(
                        update={"candidate_assets": candidates}
                    )
                    if ambiguity is not None
                    else None,
                }
            )
        )
    return constrained


def _metric_scope_assets(
    metric_results: list[_SlotPolicyResult],
    active: dict[str, list[AssetReference]],
) -> list[AssetReference]:
    """返回可以作为维度模型优先级依据的指标资产。"""

    active_metrics = [
        asset
        for item in metric_results
        for asset in active[item.decision.subquery_id]
    ]
    model_ids = {
        asset.model_id for asset in active_metrics if asset.model_id is not None
    }
    if len(model_ids) <= 1:
        return active_metrics

    # 多个指标仍有歧义时，不能凭一个候选模型替其他指标做决定。
    if any(
        item.decision.status != RetrievalDecisionStatus.RESOLVED
        for item in metric_results
    ):
        return []
    return [
        asset
        for item in metric_results
        for asset in item.decision.selected_assets
    ]


def _metric_dimension_assets_compatible(
    metric: AssetReference,
    dimension: AssetReference,
    hits_by_asset: dict[tuple[str, int, int | None], RetrievalHit],
) -> bool:
    if metric.model_id is not None and metric.model_id == dimension.model_id:
        return True
    metric_hit = hits_by_asset.get(_reference_key(metric))
    if metric_hit is None:
        return False
    compatible_dimension_ids = {
        int(value)
        for value in metric_hit.metadata.get("compatible_dimension_ids", [])
        if isinstance(value, int) and value > 0
    }
    return dimension.asset_id in compatible_dimension_ids


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


def bind_default_time_dimensions(
    request: RetrievalRequest,
    bundle: RetrievalBundle,
    schema: DatasetSchema,
) -> RetrievalBundle:
    """为已解析的单模型查询确定性绑定默认时间维度。"""

    time_range = request.intent.time_range
    if str(time_range.get("value_status") or "").lower() != "provided":
        return bundle
    normalized = time_range.get("normalized")
    if not isinstance(normalized, dict) or normalized.get("kind") != "absolute_range":
        return bundle

    metric_by_id = {metric.id: metric for metric in schema.metrics}
    dimension_by_id = {dimension.id: dimension for dimension in schema.dimensions}
    selected_metrics = [
        asset
        for decision in bundle.decision.slot_decisions
        for asset in decision.selected_assets
        if asset.asset_type == RetrievalResourceType.METRIC
    ]
    metric_model_ids = sorted(
        {
            model_id
            for asset in selected_metrics
            if (
                model_id := asset.model_id
                or getattr(metric_by_id.get(asset.asset_id), "model", None)
            )
            is not None
        }
    )
    selected_dimension_ids = {
        asset.asset_id
        for decision in bundle.decision.slot_decisions
        for asset in decision.selected_assets
        if asset.asset_type == RetrievalResourceType.DIMENSION
    }
    selected_dimension_model_ids = sorted(
        {
            model_id
            for decision in bundle.decision.slot_decisions
            for asset in decision.selected_assets
            if asset.asset_type == RetrievalResourceType.DIMENSION
            and (
                model_id := asset.model_id
                or getattr(dimension_by_id.get(asset.asset_id), "model", None)
            )
            is not None
        }
    )
    target_model_ids = metric_model_ids
    binding_reason = "DEFAULT_TIME_DIMENSION_BOUND_BY_METRIC_MODEL"
    missing_reason = "TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL"
    ambiguity_reason = "MULTIPLE_DEFAULT_TIME_DIMENSIONS_FOR_METRIC_MODEL"
    if not target_model_ids:
        select_mode = str(request.intent.query_shape.get("select_mode") or "").lower()
        # 明细查询可以只选维度。只有所有已选维度唯一指向同一模型时才自动绑定，
        # 避免在跨模型或尚有歧义时猜测时间字段。
        if select_mode != "detail" or len(selected_dimension_model_ids) != 1:
            return bundle
        target_model_ids = selected_dimension_model_ids
        binding_reason = "DEFAULT_TIME_DIMENSION_BOUND_BY_DETAIL_MODEL"
        missing_reason = "TIME_DIMENSION_NOT_CONFIGURED_FOR_DETAIL_MODEL"
        ambiguity_reason = "MULTIPLE_DEFAULT_TIME_DIMENSIONS_FOR_DETAIL_MODEL"

    bound_time_models = {
        dimension.model
        for dimension_id in selected_dimension_ids
        if (dimension := dimension_by_id.get(dimension_id)) is not None
        and _is_time_dimension(dimension)
    }

    dimensions = list(bundle.bindings.dimensions)
    decisions = list(bundle.decision.slot_decisions)
    ambiguities = list(bundle.decision.ambiguities)
    allowed_assets = list(bundle.decision.allowed_asset_ids)
    reason_codes: list[str] = []
    relation_candidate_count = 0
    has_ambiguity = False
    has_missing = False
    existing_binding_keys = {
        _reference_key(hit.asset_ref) for hit in dimensions if hit.asset_ref is not None
    }
    existing_allowed_keys = {_reference_key(asset) for asset in allowed_assets}

    for model_id in target_model_ids:
        if model_id in bound_time_models:
            continue
        candidates = _default_time_candidates(schema, model_id)
        references = [
            AssetReference(
                asset_type=RetrievalResourceType.DIMENSION,
                asset_id=dimension.id,
                model_id=model_id,
            )
            for dimension in candidates
        ]
        relation_candidate_count += len(references)
        for rank, (dimension, reference) in enumerate(
            zip(candidates, references, strict=True),
            start=1,
        ):
            if _reference_key(reference) in existing_binding_keys:
                continue
            dimensions.append(
                _default_time_dimension_hit(schema, dimension, reference, rank)
            )
            existing_binding_keys.add(_reference_key(reference))

        subquery_id = f"time_dimension:model:{model_id}"
        if len(references) == 1:
            reference = references[0]
            decisions.append(
                RetrievalSlotDecision(
                    subquery_id=subquery_id,
                    purpose=RetrievalPurpose.DIMENSION,
                    status=RetrievalDecisionStatus.RESOLVED,
                    candidate_assets=references,
                    selected_assets=references,
                    reason_codes=[binding_reason],
                )
            )
            executable = ExecutableAssetReference(
                asset_type=RetrievalResourceType.DIMENSION,
                asset_id=reference.asset_id,
                model_id=reference.model_id,
            )
            if _reference_key(executable) not in existing_allowed_keys:
                allowed_assets.append(executable)
                existing_allowed_keys.add(_reference_key(executable))
            reason_codes.append(binding_reason)
        elif len(references) > 1:
            has_ambiguity = True
            decisions.append(
                RetrievalSlotDecision(
                    subquery_id=subquery_id,
                    purpose=RetrievalPurpose.DIMENSION,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=references,
                    reason_codes=[ambiguity_reason],
                )
            )
            ambiguities.append(
                RetrievalAmbiguity(
                    subquery_id=subquery_id,
                    reason_code=ambiguity_reason,
                    candidate_assets=references,
                )
            )
            reason_codes.append(ambiguity_reason)
        else:
            has_missing = True
            decisions.append(
                RetrievalSlotDecision(
                    subquery_id=subquery_id,
                    purpose=RetrievalPurpose.DIMENSION,
                    status=RetrievalDecisionStatus.MISSED,
                    reason_codes=[missing_reason],
                )
            )
            reason_codes.append(missing_reason)

    if not reason_codes:
        return bundle

    status = bundle.decision.status
    if has_ambiguity:
        status = RetrievalDecisionStatus.AMBIGUOUS
    elif has_missing and status not in {
        RetrievalDecisionStatus.MISSED,
        RetrievalDecisionStatus.AMBIGUOUS,
    }:
        status = RetrievalDecisionStatus.PARTIAL
    decision_reason_codes = [
        code
        for code in bundle.decision.reason_codes
        if not code.startswith("SEMANTIC_BINDING_")
    ]
    decision_reason_codes = list(
        dict.fromkeys(
            [
                f"SEMANTIC_BINDING_{status.value.upper()}",
                *decision_reason_codes,
                *reason_codes,
            ]
        )
    )
    diagnostics = _with_relation_diagnostic(
        bundle.diagnostics,
        candidate_count=relation_candidate_count,
    )
    # 重新构造严格 DTO，让新增关系命中继续经过白名单与候选集合校验。
    return RetrievalBundle(
        request_id=bundle.request_id,
        bindings=RetrievalBindings(
            metrics=bundle.bindings.metrics,
            dimensions=dimensions,
            values=bundle.bindings.values,
            terms=bundle.bindings.terms,
            models=bundle.bindings.models,
            schema_hits=bundle.bindings.schema_hits,
        ),
        exemplars=bundle.exemplars,
        evidence=bundle.evidence,
        decision=RetrievalDecision(
            status=status,
            slot_decisions=decisions,
            ambiguities=ambiguities,
            allowed_asset_ids=allowed_assets,
            reason_codes=decision_reason_codes,
        ),
        diagnostics=diagnostics,
    )


def _default_time_dimension_hit(
    schema: DatasetSchema,
    dimension: SchemaElement,
    reference: AssetReference,
    rank: int,
) -> RetrievalHit:
    """把 Semantic 默认时间关系记录为可追踪的确定性命中。"""

    return RetrievalHit(
        resource_id=f"relation:default-time:{dimension.id}",
        resource_type=RetrievalResourceType.DIMENSION,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id=f"headless:dataset:{schema.data_set.id}",
        source_resource_id=f"DIMENSION:{dimension.id}",
        unit_id=f"DIMENSION:{dimension.id}:role",
        content_kind="role",
        title=dimension.name,
        snippet=dimension.description or dimension.name,
        scores=RetrievalScores(final=1.0),
        ranks_by_channel={RetrievalChannel.RELATION: rank},
        matched_field="is_default_time",
        matched_text=dimension.name,
        metadata={
            "asset_id": dimension.id,
            "model_id": dimension.model,
            "is_default_time": True,
        },
        provenance={"binding": "metric_model_default_time"},
        source_version=str(
            schema.data_set.ext_info.get("schema_version") or "headless-schema"
        ),
        asset_ref=reference,
    )


def _default_time_candidates(
    schema: DatasetSchema,
    model_id: int,
) -> list[SchemaElement]:
    """模型显式默认字段优先；未配置时使用同模型默认时间维度标记。"""

    model = next(
        (item for item in schema.models if item.get("id") == model_id),
        None,
    )
    default_field = (
        str((model or {}).get("default_time_field") or "").strip().casefold()
    )
    model_dimensions = [
        dimension for dimension in schema.dimensions if dimension.model == model_id
    ]
    if default_field:
        return sorted(
            (
                dimension
                for dimension in model_dimensions
                if default_field
                in {
                    dimension.name.strip().casefold(),
                    dimension.biz_name.strip().casefold(),
                    str(dimension.ext_info.get("field_name") or "").strip().casefold(),
                }
            ),
            key=lambda item: item.id,
        )
    return sorted(
        (
            dimension
            for dimension in model_dimensions
            if bool(dimension.ext_info.get("is_default_time"))
        ),
        key=lambda item: item.id,
    )


def _is_time_dimension(dimension: SchemaElement) -> bool:
    ext_info = dimension.ext_info
    dimension_type = str(ext_info.get("dimension_type") or "").lower()
    semantic_type = str(ext_info.get("semantic_type") or "").lower()
    data_type = str(ext_info.get("dimension_data_type") or "").lower()
    return bool(
        ext_info.get("is_default_time")
        or ext_info.get("time_granularities")
        or dimension_type in {"time", "partition_time"}
        or semantic_type == "time"
        or any(token in data_type for token in ("date", "time", "timestamp"))
    )


def _with_relation_diagnostic(
    diagnostics: RetrievalDiagnostics,
    *,
    candidate_count: int,
) -> RetrievalDiagnostics:
    channels = list(diagnostics.channels)
    existing_index = next(
        (
            index
            for index, item in enumerate(channels)
            if item.channel == RetrievalChannel.RELATION
        ),
        None,
    )
    relation = RetrievalChannelDiagnostic(
        channel=RetrievalChannel.RELATION,
        status=RetrievalChannelStatus.SUCCEEDED,
        candidate_count=candidate_count,
    )
    if existing_index is None:
        channels.append(relation)
    else:
        existing = channels[existing_index]
        channels[existing_index] = existing.model_copy(
            update={
                "status": RetrievalChannelStatus.SUCCEEDED,
                "candidate_count": existing.candidate_count + candidate_count,
            }
        )
    return diagnostics.model_copy(update={"channels": channels})


def _reference_key(
    asset: AssetReference | ExecutableAssetReference,
) -> tuple[str, int, int | None]:
    return (asset.asset_type.value, asset.asset_id, asset.model_id)


__all__ = [
    "CandidateReranker",
    "RerankCandidate",
    "RerankScore",
    "SemanticBindingPolicy",
    "SemanticBindingPolicyResult",
    "bind_default_time_dimensions",
]
