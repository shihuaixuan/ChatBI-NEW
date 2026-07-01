import numpy as np

from apps.headless import schemas
from apps.headless.api import router
from apps.headless.models import HeadlessAssetEmbedding


def test_headless_router_exposes_schema_and_mapping_contracts():
    paths = {route.path for route in router.routes}

    assert "/headless/datasources" in paths
    assert "/headless/datasources/{datasource_id}/tables" in paths
    assert "/headless/datasources/{datasource_id}/tables/{table_name}/columns" in paths
    assert "/headless/models/build-schema" in paths
    assert "/headless/models/create-with-assets" in paths
    assert "/headless/model-relations" in paths
    assert "/headless/metrics/batch-create-from-measures" in paths
    assert "/headless/datasets/{dataset_id}/schema" in paths
    assert "/headless/datasets/{dataset_id}/ontology" in paths
    assert "/headless/schema/map" in paths
    assert "/headless/knowledge/rebuild" in paths


def test_headless_router_exposes_asset_crud_contracts():
    routes = {(route.path, tuple(sorted(route.methods))) for route in router.routes}

    for path in [
        "/headless/domains/{domain_id}",
        "/headless/models/{model_id}",
        "/headless/model-relations/{relation_id}",
        "/headless/metrics/{metric_id}",
        "/headless/dimensions/{dimension_id}",
        "/headless/datasets/{dataset_id}",
        "/headless/terms/{term_id}",
    ]:
        assert (path, ("DELETE",)) in routes
        assert (path, ("PUT",)) in routes


def test_headless_router_exposes_metric_embedding_contracts():
    paths = {route.path for route in router.routes}

    assert "/headless/datasets/{dataset_id}/metric-embeddings/rebuild" in paths
    assert "/headless/datasets/{dataset_id}/metric-embeddings" in paths


def test_metric_embedding_list_returns_status_dto_without_vector_payload():
    route = next(
        route
        for route in router.routes
        if route.path == "/headless/datasets/{dataset_id}/metric-embeddings"
    )

    assert route.response_model == list[schemas.MetricEmbeddingStatusResponse]

    record = HeadlessAssetEmbedding(
        id=1,
        oid=1,
        dataset_id=20,
        asset_type="METRIC",
        asset_id=100,
        document_id=900,
        embedding_text="指标名称: 销售额",
        embedding_text_hash="hash-sales",
        embedding=np.array([0.1, 0.2]),
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dim=2,
        status="SUCCEEDED",
    )

    payload = schemas.MetricEmbeddingStatusResponse.model_validate(record).model_dump()

    assert payload["status"] == "SUCCEEDED"
    assert "embedding" not in payload
