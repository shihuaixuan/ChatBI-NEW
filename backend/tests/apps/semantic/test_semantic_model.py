from sqlalchemy.dialects.postgresql import JSONB

from apps.semantic.models.semantic_model import (
    AssetOrigin,
    AssetStatus,
    AssetType,
    ChatRecordSemanticAsset,
    SemanticAssetAudit,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    UsageRole,
)


def test_semantic_models_are_mapped_to_expected_tables():
    assert SemanticMetric.__tablename__ == "semantic_metric"
    assert SemanticDimension.__tablename__ == "semantic_dimension"
    assert SemanticDimensionValue.__tablename__ == "semantic_dimension_value"
    assert SemanticAssetAudit.__tablename__ == "semantic_asset_audit"
    assert ChatRecordSemanticAsset.__tablename__ == "chat_record_semantic_asset"


def test_asset_status_enum_contains_required_values():
    assert {status.value for status in AssetStatus} >= {
        "CANDIDATE",
        "APPROVED",
        "DISABLED",
        "DEPRECATED",
    }
    assert AssetOrigin.FIELD_INIT.value == "FIELD_INIT"
    assert AssetType.METRIC.value == "METRIC"
    assert UsageRole.INJECTED.value == "INJECTED"


def test_metric_and_dimension_json_and_embedding_columns():
    metric_columns = SemanticMetric.__table__.columns
    dimension_columns = SemanticDimension.__table__.columns

    assert isinstance(metric_columns["aliases"].type, JSONB)
    assert isinstance(metric_columns["related_dimension_ids"].type, JSONB)
    assert "VECTOR" in str(metric_columns["embedding"].type).upper()

    assert isinstance(dimension_columns["aliases"].type, JSONB)
    assert isinstance(dimension_columns["time_granularities"].type, JSONB)
    assert isinstance(dimension_columns["default_values"].type, JSONB)
    assert "VECTOR" in str(dimension_columns["embedding"].type).upper()


def test_chat_record_semantic_asset_has_required_unique_index():
    indexes = {
        index.name: {
            "unique": index.unique,
            "columns": [column.name for column in index.columns],
        }
        for index in ChatRecordSemanticAsset.__table__.indexes
    }

    assert indexes["ux_chat_record_semantic_asset"] == {
        "unique": True,
        "columns": ["record_id", "asset_type", "asset_id", "role"],
    }
