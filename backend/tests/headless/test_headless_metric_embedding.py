from apps.headless.models import HeadlessAssetEmbedding


def test_headless_asset_embedding_model_defaults():
    record = HeadlessAssetEmbedding(
        oid=1,
        dataset_id=20,
        asset_type="METRIC",
        asset_id=100,
        embedding_text="指标名称: 销售额",
        embedding_text_hash="hash-sales",
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
        embedding=[0.1, 0.2, 0.3],
    )

    assert record.status == "SUCCEEDED"
    assert record.embedding_batch_id is None
    assert record.error_message is None
