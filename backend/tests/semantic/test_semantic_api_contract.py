from apps.semantic.api.router import router


def test_semantic_router_exposes_schema_and_mapping_contracts():
    paths = {route.path for route in router.routes}

    assert "/semantic/datasources" in paths
    assert "/semantic/datasources/{datasource_id}/tables" in paths
    assert "/semantic/datasources/{datasource_id}/tables/{table_name}/columns" in paths
    assert "/semantic/models/build-schema" in paths
    assert "/semantic/models/create-with-assets" in paths
    assert "/semantic/model-relations" in paths
    assert "/semantic/metrics/batch-create-from-measures" in paths
    assert "/semantic/datasets/{dataset_id}/schema" in paths
    assert "/semantic/datasets/{dataset_id}/ontology" in paths
    assert "/semantic/schema/map" in paths
    assert "/semantic/datasets/{dataset_id}/index/rebuild" in paths


def test_semantic_router_exposes_asset_crud_contracts():
    routes = {(route.path, tuple(sorted(route.methods))) for route in router.routes}

    for path in [
        "/semantic/domains/{domain_id}",
        "/semantic/models/{model_id}",
        "/semantic/model-relations/{relation_id}",
        "/semantic/metrics/{metric_id}",
        "/semantic/dimensions/{dimension_id}",
        "/semantic/datasets/{dataset_id}",
        "/semantic/terms/{term_id}",
    ]:
        assert (path, ("DELETE",)) in routes
        assert (path, ("PUT",)) in routes


def test_semantic_router_only_exposes_unified_retrieval_rebuild_contract():
    paths = {route.path for route in router.routes}

    assert "/semantic/datasets/{dataset_id}/index/rebuild" in paths
    assert "/semantic/knowledge/rebuild" not in paths
    assert "/semantic/datasets/{dataset_id}/metric-embeddings/rebuild" not in paths
    assert "/semantic/datasets/{dataset_id}/metric-embeddings" not in paths
    assert "/semantic/datasets/{dataset_id}/asset-documents" not in paths
