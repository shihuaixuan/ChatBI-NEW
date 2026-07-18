"""RetrievalBundle 到 Graph/Agent 业务契约的投影测试。"""

from apps.retrieval.payload import bundle_to_semantic_payload
from apps.retrieval.schemas import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
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
from apps.retrieval.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
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
