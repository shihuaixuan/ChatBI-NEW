from typing import Protocol

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetSchema, SemanticDatasetReference


class DatasetSchemaReader(Protocol):
    def get_dataset_schema(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> DatasetSchema: ...


class SemanticDatasetReferenceService:
    """向其他领域提供最小的数据集引用校验信息。"""

    def __init__(self, schema_service: DatasetSchemaReader) -> None:
        self._schema_service = schema_service

    def get(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SemanticDatasetReference | None:
        try:
            schema: DatasetSchema = self._schema_service.get_dataset_schema(
                workspace_id,
                dataset_id,
            )
        except SemanticNotFoundError:
            return None
        return SemanticDatasetReference(
            dataset_id=schema.data_set.id,
            datasource_ids=tuple(
                sorted(
                    {
                        int(value)
                        for model in schema.models
                        if (value := model.get("datasource_id")) is not None
                    }
                )
            ),
            metric_ids=tuple(sorted({item.id for item in schema.metrics})),
            dimension_ids=tuple(sorted({item.id for item in schema.dimensions})),
        )
