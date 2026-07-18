from __future__ import annotations

from apps.semantic.repository.dataset_index_repository import (
    DatasetIndexGateway,
    DatasetIndexRebuildResult,
    DatasetIndexRepository,
    DatasetSchemaReader,
)


class SemanticDatasetIndexService:
    """数据集统一检索索引应用服务。"""

    def __init__(
        self,
        repository: DatasetIndexRepository,
        schema_reader: DatasetSchemaReader,
        index_gateway: DatasetIndexGateway,
    ):
        self._repository = repository
        self._schema_reader = schema_reader
        self._index_gateway = index_gateway

    def rebuild_index(
        self,
        oid: int,
        dataset_id: int,
    ) -> DatasetIndexRebuildResult:
        version = self._repository.stage_rebuild(oid, dataset_id)
        schema = self._schema_reader.build_dataset_schema(oid, dataset_id)
        enqueue_result = self._index_gateway.enqueue_dataset_rebuild(
            tenant_id=oid,
            version=version,
            schema=schema,
        )
        self._repository.commit_rebuild()
        return DatasetIndexRebuildResult(
            dataset_id=version.dataset_id,
            index_version=version.index_version,
            source_id=enqueue_result.source_id,
            generation=enqueue_result.generation,
            job_ids=enqueue_result.job_ids,
        )
