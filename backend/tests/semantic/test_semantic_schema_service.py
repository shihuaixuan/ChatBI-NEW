import pytest

from apps.semantic.errors import SemanticNotFoundError
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
    def load(self, _oid, _dataset_id):
        raise ValueError("SEMANTIC_DATASET_NOT_FOUND")
