"""semantic_binding 重排、门控与编译白名单测试。"""

from __future__ import annotations

import pytest

from apps.retrieval.compilation import validate_compilation_assets
from apps.retrieval.errors import (
    RetrievalPermissionError,
    RetrievalProviderUnavailableError,
    RetrievalQueryError,
)
from apps.retrieval.hybrid import HybridRecallResult, SubQueryRecallResult
from apps.retrieval.payload import bundle_to_semantic_payload
from apps.retrieval.planner import RetrievalQueryPlan
from apps.retrieval.policy import (
    RerankCandidate,
    RerankScore,
    SemanticBindingPolicy,
    bind_default_time_dimensions,
)
from apps.retrieval.schemas import (
    AssetReference,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalDecisionStatus,
    RetrievalHit,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalScores,
    RetrievalSourceType,
    RetrievalSubQuery,
)
from apps.retrieval.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
from apps.semantic.models.dto import DatasetSchema, SchemaElement


def _hit(
    asset_id: int,
    title: str,
    *,
    resource_type: RetrievalResourceType = RetrievalResourceType.METRIC,
    model_id: int = 10,
    exact: float | None = None,
    alias: float | None = None,
    lexical: float | None = None,
    dense: float | None = None,
    final: float = 0.01,
    metadata: dict | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        resource_id=f"{resource_type.value}:{asset_id}",
        resource_type=resource_type,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id="source-1",
        source_resource_id=f"{resource_type.value}:{asset_id}",
        unit_id=f"unit-{resource_type.value}-{asset_id}",
        content_kind="identity",
        title=title,
        scores=RetrievalScores(
            exact=exact,
            alias=alias,
            lexical=lexical,
            dense=dense,
            final=final,
        ),
        metadata=metadata or {"model_id": model_id},
        provenance={"index_generation": "generation-1"},
        source_version="schema-1",
        asset_ref=AssetReference(
            asset_type=resource_type,
            asset_id=asset_id,
            model_id=model_id,
        ),
    )


def _slot(
    subquery_id: str,
    purpose: RetrievalPurpose,
    hits: list[RetrievalHit],
    *,
    required: bool = True,
    channels: list[RetrievalChannelDiagnostic] | None = None,
    fast_path: bool = False,
) -> SubQueryRecallResult:
    return SubQueryRecallResult(
        subquery=RetrievalSubQuery(
            subquery_id=subquery_id,
            purpose=purpose,
            text=hits[0].title if hits else subquery_id,
            required=required,
        ),
        hits=tuple(hits),
        channels=tuple(
            channels
            or [
                RetrievalChannelDiagnostic(
                    channel=RetrievalChannel.EXACT,
                    status=RetrievalChannelStatus.SUCCEEDED,
                    candidate_count=len(hits),
                )
            ]
        ),
        fast_path=fast_path,
    )


def _recall(*slots: SubQueryRecallResult) -> HybridRecallResult:
    return HybridRecallResult(
        request_id="policy-1",
        plan=RetrievalQueryPlan(
            request_id="policy-1",
            profile=RetrievalProfileName.SEMANTIC_BINDING,
            subqueries=tuple(slot.subquery for slot in slots),
            fingerprint="0" * 64,
        ),
        slots=slots,
        index_generations=("generation-1",),
        total_latency_ms=12,
    )


def _time_request() -> RetrievalRequest:
    return RetrievalRequest(
        request_id="time-binding",
        tenant_id=1,
        actor_id=2,
        original_question="今天的销售额",
        rewritten_question="今天的销售额",
        intent=RetrievalIntent(
            intent_type="metric_query",
            metric_mentions=["销售额"],
            time_mentions=["今天"],
            time_range={
                "raw": "今天",
                "value_status": "provided",
                "normalized": {
                    "kind": "single_date",
                    "anchor": "today",
                    "offset_days": 0,
                    "timezone": "Asia/Shanghai",
                },
            },
        ),
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )


def _time_schema(
    *,
    default_dimension_count: int = 1,
    default_time_field: str | None = None,
) -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            model=None,
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        ),
        models=[
            {
                "id": 10,
                "name": "销售模型",
                "biz_name": "sales_model",
                "default_time_field": default_time_field,
            }
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="销售额",
                biz_name="sales_amount",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=200 + index,
                name=f"统计日期{index + 1}",
                biz_name=f"stat_date_{index + 1}",
                type="DIMENSION",
                ext_info={"dimension_type": "partition_time", "is_default_time": True},
            )
            for index in range(default_dimension_count)
        ],
    )


def test_unique_exact_match_resolves_and_generates_compilation_allowlist():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0)],
                fast_path=True,
            )
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    assert [item.asset_id for item in result.bundle.decision.allowed_asset_ids] == [100]
    assert validate_compilation_assets(
        result.bundle.decision,
        metric_ids=[100],
        dimension_ids=[],
    ) == tuple(result.bundle.decision.allowed_asset_ids)


def test_time_range_binds_metric_model_default_time_dimension():
    request = _time_request()
    schema = _time_schema()
    retrieval = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0)],
                fast_path=True,
            )
        )
    )

    bundle = bind_default_time_dimensions(request, retrieval.bundle, schema)
    payload = bundle_to_semantic_payload(request, bundle, schema)

    assert bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    assert [item.asset_id for item in bundle.decision.allowed_asset_ids] == [100, 200]
    assert bundle.decision.slot_decisions[-1].reason_codes == [
        "DEFAULT_TIME_DIMENSION_BOUND_BY_METRIC_MODEL"
    ]
    assert payload["selected_assets"]["time_dimensions"][0]["asset_id"] == 200
    assert payload["slot_bindings"]["time_filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "display_name": "统计日期1",
            "biz_name": "stat_date_1",
            "confidence": 1.0,
            "source": "intent_time_range",
            "operator": "=",
            "value": request.intent.time_range["normalized"],
        }
    ]


def test_multiple_default_time_dimensions_require_semantic_clarification():
    request = _time_request()
    schema = _time_schema(default_dimension_count=2)
    retrieval = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0)],
                fast_path=True,
            )
        )
    )

    bundle = bind_default_time_dimensions(request, retrieval.bundle, schema)

    assert bundle.decision.status == RetrievalDecisionStatus.AMBIGUOUS
    assert [item.asset_id for item in bundle.decision.allowed_asset_ids] == [100]
    assert bundle.decision.ambiguities[-1].reason_code == (
        "MULTIPLE_DEFAULT_TIME_DIMENSIONS_FOR_METRIC_MODEL"
    )


def test_model_default_time_field_disambiguates_multiple_time_dimensions():
    request = _time_request()
    schema = _time_schema(
        default_dimension_count=2,
        default_time_field="stat_date_2",
    )
    retrieval = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0)],
                fast_path=True,
            )
        )
    )

    bundle = bind_default_time_dimensions(request, retrieval.bundle, schema)

    assert bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    assert [item.asset_id for item in bundle.decision.allowed_asset_ids] == [100, 201]


def test_missing_default_time_dimension_blocks_semantic_execution():
    request = _time_request()
    schema = _time_schema(default_dimension_count=0)
    retrieval = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0)],
                fast_path=True,
            )
        )
    )

    bundle = bind_default_time_dimensions(request, retrieval.bundle, schema)

    assert bundle.decision.status == RetrievalDecisionStatus.PARTIAL
    assert bundle.decision.slot_decisions[-1].reason_codes == [
        "TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL"
    ]


def test_multiple_identity_matches_are_ambiguous_and_not_auto_bound():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [
                    _hit(100, "客户数", exact=1.0, final=0.03),
                    _hit(101, "客户数", alias=1.0, final=0.02),
                ],
            )
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.AMBIGUOUS
    assert result.bundle.decision.allowed_asset_ids == []
    assert (
        result.bundle.decision.ambiguities[0].reason_code == "MULTIPLE_IDENTITY_MATCHES"
    )


def test_dimension_identity_matches_use_selected_metric_model_to_resolve():
    metric = _hit(
        100,
        "销售下单客户数",
        exact=1.0,
        model_id=10,
        metadata={"model_id": 10, "compatible_dimension_ids": [200]},
    )
    same_model_dimension = _hit(
        200,
        "档口ID",
        resource_type=RetrievalResourceType.DIMENSION,
        model_id=10,
        alias=1.0,
        final=0.03,
    )
    other_model_dimension = _hit(
        201,
        "档口ID",
        resource_type=RetrievalResourceType.DIMENSION,
        model_id=11,
        alias=1.0,
        final=0.02,
    )

    result = SemanticBindingPolicy().apply(
        _recall(
            _slot("metric:1", RetrievalPurpose.METRIC, [metric]),
            _slot(
                "dimension:1",
                RetrievalPurpose.DIMENSION,
                [same_model_dimension, other_model_dimension],
            ),
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    dimension_decision = result.bundle.decision.slot_decisions[1]
    assert [item.asset_id for item in dimension_decision.selected_assets] == [200]
    assert dimension_decision.reason_codes == [
        "IDENTITY_DISAMBIGUATED_BY_METRIC_MODEL_COMPATIBILITY"
    ]


def test_model_compatibility_does_not_resolve_lexical_only_ambiguity():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售下单客户数", exact=1.0, model_id=10)],
            ),
            _slot(
                "dimension:1",
                RetrievalPurpose.DIMENSION,
                [
                    _hit(
                        200,
                        "档口ID",
                        resource_type=RetrievalResourceType.DIMENSION,
                        model_id=10,
                        lexical=0.9,
                        final=0.03,
                    ),
                    _hit(
                        201,
                        "商家ID",
                        resource_type=RetrievalResourceType.DIMENSION,
                        model_id=11,
                        lexical=0.9,
                        final=0.02,
                    ),
                ],
            ),
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.AMBIGUOUS
    assert result.bundle.decision.slot_decisions[1].selected_assets == []


def test_partial_and_below_absolute_threshold_have_explicit_reasons():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1", RetrievalPurpose.METRIC, [_hit(100, "销售额", exact=1.0)]
            ),
            _slot(
                "dimension:1",
                RetrievalPurpose.DIMENSION,
                [
                    _hit(
                        200,
                        "门店",
                        resource_type=RetrievalResourceType.DIMENSION,
                        lexical=0.4,
                    )
                ],
            ),
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.PARTIAL
    assert result.bundle.decision.slot_decisions[1].reason_codes == [
        "TOP1_BELOW_ABSOLUTE_THRESHOLD"
    ]


@pytest.mark.parametrize(
    ("hits", "expected_reason"),
    [
        (
            [
                _hit(100, "客户数", dense=0.90, final=0.03),
                _hit(101, "活跃客户数", dense=0.85, final=0.02),
            ],
            "TOP1_TOP2_GAP_BELOW_THRESHOLD",
        ),
        (
            [
                _hit(100, "客户数", lexical=0.90, final=0.03),
                _hit(101, "活跃客户数", dense=0.90, final=0.02),
            ],
            "TOP_CANDIDATES_USE_INCOMPARABLE_SCORE_CHANNELS",
        ),
    ],
)
def test_semantic_candidates_only_compare_gap_within_same_score_channel(
    hits: list[RetrievalHit],
    expected_reason: str,
):
    result = SemanticBindingPolicy().apply(
        _recall(_slot("metric:1", RetrievalPurpose.METRIC, hits))
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.AMBIGUOUS
    assert result.bundle.decision.ambiguities[0].reason_code == expected_reason


def test_selected_assets_from_distinct_metric_models_require_split():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", exact=1.0, model_id=10)],
            ),
            _slot(
                "metric:2",
                RetrievalPurpose.METRIC,
                [_hit(101, "欠款额", exact=1.0, model_id=11)],
            ),
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.CROSS_MODEL
    assert {item.asset_id for item in result.bundle.decision.allowed_asset_ids} == {
        100,
        101,
    }


def test_joinable_dimension_uses_resource_relationship_metadata():
    metric = _hit(
        100,
        "销售额",
        exact=1.0,
        model_id=10,
        metadata={
            "model_id": 10,
            "joinable_model_ids": [11],
            "compatible_dimension_ids": [200, 201],
        },
    )
    dimension = _hit(
        200,
        "地区",
        resource_type=RetrievalResourceType.DIMENSION,
        exact=1.0,
        model_id=11,
    )

    result = SemanticBindingPolicy().apply(
        _recall(
            _slot("metric:1", RetrievalPurpose.METRIC, [metric]),
            _slot("dimension:1", RetrievalPurpose.DIMENSION, [dimension]),
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED


class _SelectingReranker:
    provider = "test"
    model = "reranker-test"

    def rerank(
        self,
        _query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> list[RerankScore]:
        return [
            RerankScore(candidate_id=candidates[0].candidate_id, score=0.73),
            RerankScore(candidate_id=candidates[1].candidate_id, score=0.92),
        ]


def test_reranker_can_only_reorder_existing_candidates():
    result = SemanticBindingPolicy(_SelectingReranker()).apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [
                    _hit(100, "销售额", dense=0.8, final=0.03),
                    _hit(101, "净销售额", dense=0.79, final=0.02),
                ],
            )
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    assert result.bundle.decision.allowed_asset_ids[0].asset_id == 101
    rerank = next(
        item
        for item in result.bundle.diagnostics.channels
        if item.channel == RetrievalChannel.RERANK
    )
    assert rerank.status == RetrievalChannelStatus.SUCCEEDED


class _InvalidReranker:
    provider = "test"
    model = "invalid-test"

    def rerank(
        self,
        _query: str,
        _candidates: tuple[RerankCandidate, ...],
    ) -> list[RerankScore]:
        return [RerankScore(candidate_id="invented-resource", score=0.99)]


def test_reranker_cannot_generate_asset_ids():
    with pytest.raises(RetrievalQueryError, match="UNKNOWN_CANDIDATE"):
        SemanticBindingPolicy(_InvalidReranker()).apply(
            _recall(
                _slot(
                    "metric:1",
                    RetrievalPurpose.METRIC,
                    [_hit(100, "销售额", dense=0.8)],
                )
            )
        )


class _UnavailableReranker:
    provider = "test"
    model = "unavailable-test"

    def rerank(
        self,
        _query: str,
        _candidates: tuple[RerankCandidate, ...],
    ) -> list[RerankScore]:
        raise RetrievalProviderUnavailableError(
            "reranker unavailable",
            details={"reason_code": "RERANKER_UNAVAILABLE"},
        )


def test_required_provider_unavailable_is_explicitly_degraded():
    result = SemanticBindingPolicy(_UnavailableReranker()).apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", lexical=0.9)],
            )
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.DEGRADED
    assert result.bundle.diagnostics.degraded_reason == "RERANKER_UNAVAILABLE"


def test_unavailable_dense_channel_is_degraded_even_when_lexical_candidate_resolves():
    result = SemanticBindingPolicy().apply(
        _recall(
            _slot(
                "metric:1",
                RetrievalPurpose.METRIC,
                [_hit(100, "销售额", lexical=0.9)],
                channels=[
                    RetrievalChannelDiagnostic(
                        channel=RetrievalChannel.LEXICAL,
                        status=RetrievalChannelStatus.SUCCEEDED,
                        candidate_count=1,
                    ),
                    RetrievalChannelDiagnostic(
                        channel=RetrievalChannel.DENSE,
                        status=RetrievalChannelStatus.UNAVAILABLE,
                        error_code="EMBEDDING_PROVIDER_MISSING",
                    ),
                ],
            )
        )
    )

    assert result.bundle.decision.status == RetrievalDecisionStatus.DEGRADED
    assert result.bundle.diagnostics.degraded_reason == "EMBEDDING_PROVIDER_MISSING"


def test_compiler_rejects_non_executable_decision_and_assets_outside_allowlist():
    ambiguous = (
        SemanticBindingPolicy()
        .apply(
            _recall(
                _slot(
                    "metric:1",
                    RetrievalPurpose.METRIC,
                    [_hit(100, "客户数", exact=1.0), _hit(101, "客户数", exact=1.0)],
                )
            )
        )
        .bundle.decision
    )
    with pytest.raises(RetrievalQueryError, match="DECISION_NOT_EXECUTABLE"):
        validate_compilation_assets(ambiguous, metric_ids=[100], dimension_ids=[])

    resolved = (
        SemanticBindingPolicy()
        .apply(
            _recall(
                _slot(
                    "metric:1",
                    RetrievalPurpose.METRIC,
                    [_hit(100, "销售额", exact=1.0)],
                )
            )
        )
        .bundle.decision
    )
    with pytest.raises(RetrievalPermissionError, match="未由语义绑定决策放行"):
        validate_compilation_assets(resolved, metric_ids=[999], dimension_ids=[])
