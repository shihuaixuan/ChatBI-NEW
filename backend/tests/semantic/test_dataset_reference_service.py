from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.dataset_reference_service import (
    SemanticDatasetReferenceService,
)


class FakeSchemaReader:
    def get_dataset_schema(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> DatasetSchema:
        if workspace_id != 3 or dataset_id != 30:
            raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
        dataset = SchemaElement(
            data_set_id=30,
            data_set_name="销售数据集",
            id=30,
            name="销售数据集",
            biz_name="sales_dataset",
            type="DATASET",
        )
        return DatasetSchema(
            data_set=dataset,
            models=[
                {"id": 1, "datasource_id": 8},
                {"id": 2, "datasource_id": 8},
                {"id": 3, "datasource_id": 9},
            ],
            metrics=[
                dataset.model_copy(
                    update={"id": 100, "type": "METRIC", "model": 1}
                )
            ],
            dimensions=[
                dataset.model_copy(
                    update={"id": 200, "type": "DIMENSION", "model": 1}
                )
            ],
        )


def test_dataset_reference_exposes_only_stable_scope_fields():
    service = SemanticDatasetReferenceService(FakeSchemaReader())

    reference = service.get(3, 30)

    assert reference is not None
    assert reference.dataset_id == 30
    assert reference.datasource_ids == (8, 9)
    assert reference.metric_ids == (100,)
    assert reference.dimension_ids == (200,)


def test_dataset_reference_returns_none_for_invisible_dataset():
    service = SemanticDatasetReferenceService(FakeSchemaReader())

    assert service.get(3, 99) is None
