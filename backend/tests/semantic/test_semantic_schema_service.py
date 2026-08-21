import pytest

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.schema_service import (
    SemanticSchemaService,
)


def test_get_dataset_schema_maps_repository_error_to_not_found():
    with pytest.raises(SemanticNotFoundError) as exc_info:
        SemanticSchemaService(_MissingDatasetRepository()).get_dataset_schema(
            oid=1, dataset_id=9
        )

    assert exc_info.value.detail == "SEMANTIC_DATASET_NOT_FOUND"


class _MissingDatasetRepository:
    def load_published_schema(self, _oid, _dataset_id):
        raise ValueError("SEMANTIC_DATASET_NOT_FOUND")


def test_schema_service_reads_immutable_published_snapshot() -> None:
    published = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=9,
            data_set_name="已发布数据集",
            id=9,
            name="已发布数据集",
            biz_name="published_dataset",
            type="DATASET",
        ),
        schema_version=3,
        contract_version=2,
        schema_fingerprint="published-schema-v2",
    )

    class PublishedSchemaRepository:
        def load_published_schema(self, _oid, _dataset_id):
            return published.model_dump(mode="json")

    schema = SemanticSchemaService(PublishedSchemaRepository()).build_dataset_schema(
        1, 9
    )

    assert schema.schema_fingerprint == "published-schema-v2"
    assert schema.contract_version == 2
