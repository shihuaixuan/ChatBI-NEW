"""P0-4 VALUE 槽值归一：规划、决策三分支与 canonical 替换。"""

from __future__ import annotations

from typing import Any

from apps.retrieval.models.dto import (
    AssetReference,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalDecisionStatus,
    RetrievalDimensionSlot,
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
    SemanticClarificationBinding,
)
from apps.retrieval.projection.payload import (
    apply_decision_to_semantic_payload,
    bundle_to_semantic_payload,
)
from apps.retrieval.projection.planner import (
    SemanticBindingQueryPlanner,
    value_lookup_slots,
)
from apps.retrieval.query.decision import apply_semantic_clarification
from apps.retrieval.query.hybrid import HybridRecallResult, SubQueryRecallResult
from apps.retrieval.query.policy import SemanticBindingPolicy
from apps.retrieval.query.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
from apps.semantic.models.dto import DatasetSchema, SchemaElement

DIMENSION_ASSET_ID = 200
METRIC_ASSET_ID = 100


def _request(*, slot_value: Any = "华东") -> RetrievalRequest:
    return RetrievalRequest(
        request_id="value-binding-1",
        tenant_id=1,
        actor_id=2,
        original_question="华东的销售额",
        rewritten_question="华东的销售额",
        intent=RetrievalIntent(
            intent_type="metric_query",
            metric_mentions=["销售额"],
            dimension_mentions=["城市"],
            dimension_slots=[
                RetrievalDimensionSlot(
                    name="城市",
                    role="filter",
                    value=slot_value,
                    value_status="provided",
                )
            ],
        ),
        scope=RetrievalScope(dataset_ids=[20], source_ids=["dataset:20"]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )


def _hit(
    asset_id: int,
    title: str,
    *,
    resource_type: RetrievalResourceType,
    exact: float | None = 1.0,
    metadata: dict[str, Any] | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        resource_id=f"{resource_type.value}:{asset_id}:{title}",
        resource_type=resource_type,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id="source-1",
        source_resource_id=f"{resource_type.value}:{asset_id}",
        unit_id=f"unit-{asset_id}-{title}",
        content_kind="value" if resource_type == RetrievalResourceType.VALUE else "identity",
        title=title,
        scores=RetrievalScores(exact=exact, final=exact or 0.01),
        metadata=metadata or {},
        provenance={"index_generation": "generation-1"},
        source_version="schema-1",
        asset_ref=AssetReference(
            asset_type=resource_type,
            asset_id=asset_id,
            model_id=10,
        ),
    )


def _slot(
    subquery_id: str,
    purpose: RetrievalPurpose,
    text: str,
    hits: list[RetrievalHit],
    *,
    required: bool = True,
) -> SubQueryRecallResult:
    return SubQueryRecallResult(
        subquery=RetrievalSubQuery(
            subquery_id=subquery_id,
            purpose=purpose,
            text=text,
            required=required,
            role="filter" if purpose == RetrievalPurpose.VALUE else None,
        ),
        hits=tuple(hits),
        channels=(
            RetrievalChannelDiagnostic(
                channel=RetrievalChannel.EXACT,
                status=RetrievalChannelStatus.SUCCEEDED,
                candidate_count=len(hits),
            ),
        ),
    )


def _base_slots(value_hits: list[RetrievalHit]) -> list[SubQueryRecallResult]:
    return [
        _slot(
            "metric:1",
            RetrievalPurpose.METRIC,
            "销售额",
            [_hit(METRIC_ASSET_ID, "销售额", resource_type=RetrievalResourceType.METRIC)],
        ),
        _slot(
            "dimension:1",
            RetrievalPurpose.DIMENSION,
            "城市",
            [_hit(DIMENSION_ASSET_ID, "城市", resource_type=RetrievalResourceType.DIMENSION)],
        ),
        _slot(
            "value:1",
            RetrievalPurpose.VALUE,
            "华东",
            value_hits,
            required=False,
        ),
    ]


def _recall(slots: list[SubQueryRecallResult]) -> HybridRecallResult:
    return HybridRecallResult(
        request_id="value-binding-1",
        plan=__import__(
            "apps.retrieval.projection.planner",
            fromlist=["RetrievalQueryPlan"],
        ).RetrievalQueryPlan(
            request_id="value-binding-1",
            profile=RetrievalProfileName.SEMANTIC_BINDING,
            subqueries=tuple(slot.subquery for slot in slots),
            fingerprint="0" * 64,
        ),
        slots=tuple(slots),
        index_generations=("generation-1",),
        total_latency_ms=5,
    )


def _schema() -> DatasetSchema:
    city = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=DIMENSION_ASSET_ID,
        name="城市",
        biz_name="city",
        type="DIMENSION",
    )
    district = city.model_copy(
        update={"id": DIMENSION_ASSET_ID + 10, "name": "商圈", "biz_name": "district"}
    )
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        ),
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=METRIC_ASSET_ID,
                name="销售额",
                biz_name="sales_amount",
                type="METRIC",
            )
        ],
        dimensions=[city, district],
        dimension_values=[city, district],
    )


def test_value_lookup_slots_filters_identifier_values():
    intent = {
        "dimension_slots": [
            {"name": "城市", "role": "filter", "value": "华东", "value_status": "provided"},
            {"name": "档口ID", "role": "filter", "value": "100011", "value_status": "provided"},
            {"name": "订单号", "role": "filter", "value": "USO202606300001", "value_status": "provided"},
            {"name": "城市", "role": "filter", "value": ["华东", "华北"], "value_status": "provided"},
            {"name": "商圈", "role": "group_by", "value": "武林", "value_status": "not_provided"},
            {"name": "渠道", "role": "filter", "value": None, "value_status": "not_provided"},
        ]
    }

    # 同维度同值去重；数字/标识符不进维值检索。
    assert value_lookup_slots(intent) == [
        ("城市", "华东"),
        ("城市", "华北"),
    ]


def test_planner_emits_optional_value_subqueries():
    plan = SemanticBindingQueryPlanner().plan(_request())

    value_slots = [
        item for item in plan.subqueries if item.purpose == RetrievalPurpose.VALUE
    ]
    assert [(item.subquery_id, item.text, item.required) for item in value_slots] == [
        ("value:1", "华东", False)
    ]
    assert value_slots[0].filters["dimension_name"] == "城市"


def test_value_miss_does_not_degrade_overall_decision():
    result = SemanticBindingPolicy().apply(_recall(_base_slots([])))

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    value_slot = next(
        slot
        for slot in result.bundle.decision.slot_decisions
        if slot.purpose == RetrievalPurpose.VALUE
    )
    assert value_slot.status == RetrievalDecisionStatus.MISSED
    payload = bundle_to_semantic_payload(_request(), result.bundle, _schema())
    dimension_filters = payload["slot_bindings"]["dimension_filters"]
    # 未命中保留原值，不猜测替换。
    assert dimension_filters[0]["value"] == "华东"
    assert "value_normalized" not in dimension_filters[0]


def test_value_ambiguity_across_dimensions_surfaces_clarification():
    """同一原始值命中不同维度的维值字典时，必须交用户消解。"""

    ambiguous_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东",
            resource_type=RetrievalResourceType.VALUE,
            metadata={"canonical_value": "EAST", "dimension_id": DIMENSION_ASSET_ID},
        ),
        _hit(
            DIMENSION_ASSET_ID + 10,
            "华东",
            resource_type=RetrievalResourceType.VALUE,
            exact=0.95,
            metadata={
                "canonical_value": "HD",
                "dimension_id": DIMENSION_ASSET_ID + 10,
            },
        ),
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(ambiguous_hits)))

    assert result.bundle.decision.status == RetrievalDecisionStatus.AMBIGUOUS
    value_ambiguity = next(
        ambiguity
        for ambiguity in result.bundle.decision.ambiguities
        if ambiguity.subquery_id == "value:1"
    )
    assert len(value_ambiguity.candidate_assets) == 2


def test_same_dimension_value_matches_collapse_deterministically():
    """同维度多值命中不是资产歧义：确定性收敛，canonical 不确定时保留原值。"""

    collapsed_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东区",
            resource_type=RetrievalResourceType.VALUE,
            metadata={"canonical_value": "EAST_1", "dimension_id": DIMENSION_ASSET_ID},
        ),
        _hit(
            DIMENSION_ASSET_ID,
            "华东大区",
            resource_type=RetrievalResourceType.VALUE,
            exact=0.95,
            metadata={"canonical_value": "EAST_2", "dimension_id": DIMENSION_ASSET_ID},
        ),
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(collapsed_hits)))

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    value_slot = next(
        slot
        for slot in result.bundle.decision.slot_decisions
        if slot.purpose == RetrievalPurpose.VALUE
    )
    assert value_slot.status == RetrievalDecisionStatus.RESOLVED
    assert value_slot.reason_codes == ["VALUE_AMBIGUITY_COLLAPSED_TO_SINGLE_DIMENSION"]
    payload = bundle_to_semantic_payload(_request(), result.bundle, _schema())
    # canonical 无法唯一确定时保留用户原值，不做猜测替换。
    assert payload["slot_bindings"]["dimension_filters"][0]["value"] == "华东"


def test_resolved_value_replaces_filter_with_canonical():
    resolved_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东",
            resource_type=RetrievalResourceType.VALUE,
            metadata={
                "canonical_value": "EAST",
                "dimension_id": DIMENSION_ASSET_ID,
                "aliases": ["华东地区"],
            },
        )
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(resolved_hits)))

    assert result.bundle.decision.status == RetrievalDecisionStatus.RESOLVED
    # VALUE 资产不进入编译白名单。
    assert all(
        item.asset_type != RetrievalResourceType.VALUE
        for item in result.bundle.decision.allowed_asset_ids
    )
    payload = bundle_to_semantic_payload(_request(), result.bundle, _schema())
    dimension_filters = payload["slot_bindings"]["dimension_filters"]
    assert dimension_filters[0]["value"] == "EAST"
    assert dimension_filters[0]["value_normalized"] is True
    assert dimension_filters[0]["original_terms"] == ["华东"]
    value_filters = payload["slot_bindings"]["value_filters"]
    assert value_filters[0]["value_normalizations"] == [
        {"original_term": "华东", "canonical_value": "EAST"}
    ]


def test_alias_matched_value_resolves_canonical():
    resolved_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东地区",
            resource_type=RetrievalResourceType.VALUE,
            metadata={
                "canonical_value": "EAST",
                "dimension_id": DIMENSION_ASSET_ID,
                "aliases": ["华东"],
            },
        )
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(resolved_hits)))
    payload = bundle_to_semantic_payload(_request(), result.bundle, _schema())

    assert payload["slot_bindings"]["dimension_filters"][0]["value"] == "EAST"


def test_value_clarification_selection_applies_canonical_after_user_choice():
    ambiguous_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东地区",
            resource_type=RetrievalResourceType.VALUE,
            metadata={
                "canonical_value": "EAST",
                "dimension_id": DIMENSION_ASSET_ID,
                "aliases": ["华东"],
            },
        ),
        _hit(
            DIMENSION_ASSET_ID + 10,
            "华东商圈",
            resource_type=RetrievalResourceType.VALUE,
            exact=0.95,
            metadata={
                "canonical_value": "HD",
                "dimension_id": DIMENSION_ASSET_ID + 10,
                "aliases": ["华东"],
            },
        ),
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(ambiguous_hits)))
    raw_payload = bundle_to_semantic_payload(_request(), result.bundle, _schema())

    clarified = apply_semantic_clarification(
        result.bundle,
        [
            SemanticClarificationBinding(
                subquery_id="value:1",
                asset_type=RetrievalResourceType.VALUE,
                asset_id=DIMENSION_ASSET_ID,
                model_id=10,
            )
        ],
    )
    assert clarified.decision.status == RetrievalDecisionStatus.RESOLVED

    updated_payload = apply_decision_to_semantic_payload(
        _request(),
        raw_payload,
        clarified,
    )
    dimension_filters = updated_payload["slot_bindings"]["dimension_filters"]
    assert dimension_filters[0]["value"] == "EAST"
    assert dimension_filters[0]["value_normalized"] is True


def test_multi_value_filters_normalize_each_item():
    request = _request(slot_value=["华东", "华北"])
    resolved_hits = [
        _hit(
            DIMENSION_ASSET_ID,
            "华东",
            resource_type=RetrievalResourceType.VALUE,
            metadata={"canonical_value": "EAST", "dimension_id": DIMENSION_ASSET_ID},
        )
    ]
    result = SemanticBindingPolicy().apply(_recall(_base_slots(resolved_hits)))
    payload = bundle_to_semantic_payload(request, result.bundle, _schema())

    dimension_filters = payload["slot_bindings"]["dimension_filters"]
    assert dimension_filters[0]["operator"] == "in"
    assert dimension_filters[0]["value"] == ["EAST", "华北"]
    assert dimension_filters[0]["original_terms"] == ["华东"]
