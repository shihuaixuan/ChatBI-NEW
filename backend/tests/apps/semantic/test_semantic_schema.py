import pytest
from pydantic import ValidationError

from apps.semantic.models.semantic_schema import (
    ChatRecordSemanticAssetInfo,
    ChatRecordSemanticAssetResponse,
    DimensionCreate,
    DimensionInfo,
    DimensionPageQuery,
    DimensionUpdate,
    DisableAssetRequest,
    InitializeSemanticRequest,
    InitializeSemanticResponse,
    MetricCreate,
    MetricInfo,
    MetricPageQuery,
    MetricUpdate,
    RebuildEmbeddingResponse,
    SemanticAssetMatch,
    SemanticRetrieveRequest,
    SemanticRetrieveResponse,
    ValidateExpressionRequest,
    ValidateExpressionResponse,
)


def test_schema_classes_are_importable():
    expected = [
        MetricCreate,
        MetricUpdate,
        MetricInfo,
        MetricPageQuery,
        DimensionCreate,
        DimensionUpdate,
        DimensionInfo,
        DimensionPageQuery,
        InitializeSemanticRequest,
        InitializeSemanticResponse,
        ValidateExpressionRequest,
        ValidateExpressionResponse,
        DisableAssetRequest,
        RebuildEmbeddingResponse,
        SemanticAssetMatch,
        SemanticRetrieveRequest,
        SemanticRetrieveResponse,
        ChatRecordSemanticAssetInfo,
        ChatRecordSemanticAssetResponse,
    ]

    assert all(schema is not None for schema in expected)


def test_metric_create_requires_core_fields_and_allows_candidate_or_disabled_status():
    metric = MetricCreate(
        datasource_id=1,
        name="sales_amount",
        display_name="销售额",
        expr="pay_amount",
        status="CANDIDATE",
    )

    assert metric.status == "CANDIDATE"

    disabled = MetricCreate(
        datasource_id=1,
        name="old_sales_amount",
        display_name="旧销售额",
        expr="old_pay_amount",
        status="DISABLED",
    )
    assert disabled.status == "DISABLED"

    with pytest.raises(ValidationError):
        MetricCreate(datasource_id=1, name="bad_metric", display_name="坏指标", status="CANDIDATE")


def test_create_schema_rejects_approved_status():
    with pytest.raises(ValidationError):
        MetricCreate(
            datasource_id=1,
            name="approved_metric",
            display_name="已审核指标",
            expr="amount",
            status="APPROVED",
        )

    with pytest.raises(ValidationError):
        DimensionCreate(
            datasource_id=1,
            name="approved_dimension",
            display_name="已审核维度",
            expr="region",
            status="APPROVED",
        )


def test_semantic_retrieve_request_defaults_top_k_and_approved_only():
    request = SemanticRetrieveRequest(oid=1, datasource_id=2, question="最近 7 天销售额趋势")

    assert request.metric_top_k == 5
    assert request.dimension_top_k == 8
    assert request.approved_only is True
