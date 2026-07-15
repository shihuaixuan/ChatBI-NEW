from apps.headless.api import router


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


def test_headless_router_only_exposes_unified_retrieval_rebuild_contract():
    paths = {route.path for route in router.routes}

    assert "/headless/knowledge/rebuild" in paths
    assert "/headless/datasets/{dataset_id}/metric-embeddings/rebuild" not in paths
    assert "/headless/datasets/{dataset_id}/metric-embeddings" not in paths
