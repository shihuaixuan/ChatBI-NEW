"""RetrievalBundle 到 Graph/Agent 业务契约的投影测试。"""

from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalDimensionSlot,
    RetrievalHit,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalSlotDecision,
    RetrievalSourceType,
)
from apps.retrieval.projection.payload import bundle_to_semantic_payload
from apps.retrieval.query.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
from apps.semantic.models.dto import DatasetSchema, SchemaElement


def _element(asset_type: str, asset_id: int, name: str, biz_name: str) -> SchemaElement:
    return SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=asset_id,
        name=name,
        biz_name=biz_name,
        type=asset_type,
    )


def test_bundle_uses_semantic_schema_as_execution_fact():
    metric = _element("METRIC", 100, "销售额", "sales_amount")
    schema = DatasetSchema(
        data_set=_element("DATASET", 20, "经营分析", "business"),
        metrics=[metric],
    )
    request = RetrievalRequest(
        request_id="semantic-binding-contract",
        tenant_id=1,
        actor_id=2,
        original_question="销售额",
        rewritten_question="销售额",
        intent=RetrievalIntent(intent_type="metric_query", metric_mentions=["销售额"]),
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )
    asset = AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=100,
        model_id=10,
    )
    bundle = RetrievalBundle(
        request_id=request.request_id,
        bindings=RetrievalBindings(
            metrics=[
                RetrievalHit(
                    resource_id="metric:100",
                    resource_type=RetrievalResourceType.METRIC,
                    source_type=RetrievalSourceType.SEMANTIC,
                    source_id="headless:20",
                    source_resource_id="metric:100",
                    unit_id="unit:100",
                    content_kind="identity",
                    title="索引中的旧标题",
                    source_version="generation-1",
                    asset_ref=asset,
                )
            ]
        ),
        decision=RetrievalDecision(
            status=RetrievalDecisionStatus.RESOLVED,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:1",
                    purpose=RetrievalPurpose.METRIC,
                    status=RetrievalDecisionStatus.RESOLVED,
                    candidate_assets=[asset],
                    selected_assets=[asset],
                )
            ],
            allowed_asset_ids=[
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType.METRIC,
                    asset_id=100,
                    model_id=10,
                )
            ],
        ),
        diagnostics=RetrievalDiagnostics(
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
            index_generation="generation-1",
        ),
    )

    payload = bundle_to_semantic_payload(request, bundle, schema)

    selected = payload["selected_assets"]["metrics"][0]
    assert payload["status"] == "hit"
    assert selected["name"] == "销售额"
    assert selected["payload"]["biz_name"] == "sales_amount"
    assert payload["decision"]["strategy"] == "semantic_binding"
    assert payload["allowed_asset_ids"][0]["asset_id"] == 100


def test_bundle_keeps_literal_dimension_value_as_filter_without_value_asset():
    metric = _element("METRIC", 100, "总下单客户数", "order_customer_cnt_total")
    dimension = _element("DIMENSION", 200, "档口ID", "stall_id")
    schema = DatasetSchema(
        data_set=_element("DATASET", 20, "商城店铺主题", "mall_store"),
        metrics=[metric],
        dimensions=[dimension],
    )
    request = RetrievalRequest(
        request_id="literal-dimension-filter",
        tenant_id=1,
        actor_id=2,
        original_question="店铺100011的总下单客户数",
        rewritten_question="店铺100011的总下单客户数",
        intent=RetrievalIntent(
            intent_type="metric_query",
            metric_mentions=["总下单客户数"],
            dimension_mentions=["店铺"],
            dimension_slots=[
                RetrievalDimensionSlot(
                    name="店铺",
                    role="filter",
                    value="100011",
                    value_status="provided",
                )
            ],
        ),
        scope=RetrievalScope(dataset_ids=[20]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )
    metric_asset = AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=100,
        model_id=10,
    )
    dimension_asset = AssetReference(
        asset_type=RetrievalResourceType.DIMENSION,
        asset_id=200,
        model_id=10,
    )
    bundle = RetrievalBundle(
        request_id=request.request_id,
        bindings=RetrievalBindings(
            metrics=[
                RetrievalHit(
                    resource_id="metric:100",
                    resource_type=RetrievalResourceType.METRIC,
                    source_type=RetrievalSourceType.SEMANTIC,
                    source_id="headless:20",
                    source_resource_id="metric:100",
                    unit_id="unit:100",
                    content_kind="identity",
                    title="总下单客户数",
                    source_version="generation-1",
                    asset_ref=metric_asset,
                )
            ],
            dimensions=[
                RetrievalHit(
                    resource_id="dimension:200",
                    resource_type=RetrievalResourceType.DIMENSION,
                    source_type=RetrievalSourceType.SEMANTIC,
                    source_id="headless:20",
                    source_resource_id="dimension:200",
                    unit_id="unit:200",
                    content_kind="identity",
                    title="档口ID",
                    matched_text="店铺",
                    source_version="generation-1",
                    asset_ref=dimension_asset,
                )
            ],
        ),
        decision=RetrievalDecision(
            status=RetrievalDecisionStatus.RESOLVED,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:1",
                    purpose=RetrievalPurpose.METRIC,
                    status=RetrievalDecisionStatus.RESOLVED,
                    candidate_assets=[metric_asset],
                    selected_assets=[metric_asset],
                ),
                RetrievalSlotDecision(
                    subquery_id="dimension:1",
                    purpose=RetrievalPurpose.DIMENSION,
                    status=RetrievalDecisionStatus.RESOLVED,
                    candidate_assets=[dimension_asset],
                    selected_assets=[dimension_asset],
                ),
            ],
            allowed_asset_ids=[
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType.METRIC,
                    asset_id=100,
                    model_id=10,
                ),
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType.DIMENSION,
                    asset_id=200,
                    model_id=10,
                ),
            ],
        ),
        diagnostics=RetrievalDiagnostics(
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
            index_generation="generation-1",
        ),
    )

    payload = bundle_to_semantic_payload(request, bundle, schema)

    assert payload["decision"]["status"] == "resolved"
    assert payload["selected_assets"]["values"] == []
    assert payload["slot_bindings"]["value_filters"] == []
    assert payload["slot_bindings"]["dimension_filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "display_name": "档口ID",
            "biz_name": "stall_id",
            "confidence": 0.0,
            "source": "intent_dimension_slot",
            "operator": "=",
            "value": "100011",
        }
    ]

    multi_value_request = request.model_copy(
        update={
            "intent": RetrievalIntent(
                intent_type="comparison_analysis",
                metric_mentions=["总下单客户数"],
                dimension_mentions=["店铺"],
                dimension_slots=[
                    RetrievalDimensionSlot(
                        name="店铺",
                        role="filter",
                        value=["100011", "100012"],
                        value_status="provided",
                    )
                ],
            )
        }
    )

    multi_value_payload = bundle_to_semantic_payload(
        multi_value_request,
        bundle,
        schema,
    )

    assert (
        multi_value_payload["slot_bindings"]["dimension_filters"][0]["operator"] == "in"
    )
    assert multi_value_payload["slot_bindings"]["dimension_filters"][0]["value"] == [
        "100011",
        "100012",
    ]
    assert multi_value_payload["slot_bindings"]["group_dimensions"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "display_name": "档口ID",
            "biz_name": "stall_id",
            "confidence": 0.0,
            "source": "semantic_binding",
        }
    ]

    share_request = request.model_copy(
        update={
            "intent": multi_value_request.intent.model_copy(
                update={"intent_type": "share_analysis"}
            )
        }
    )
    share_payload = bundle_to_semantic_payload(share_request, bundle, schema)

    assert share_payload["slot_bindings"]["dimension_filters"][0]["operator"] == "in"
    assert share_payload["slot_bindings"]["group_dimensions"][0]["asset_id"] == 200
