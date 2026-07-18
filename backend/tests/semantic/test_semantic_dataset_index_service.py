from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from apps.retrieval.semantic_worker import process_semantic_index_jobs
from apps.semantic.api import dataset_indexes
from apps.semantic.models.dto import (
    DatasetIndexEnqueueResult,
    DatasetIndexRebuildResult,
)
from apps.semantic.models.orm import SemanticDataset
from apps.semantic.repository.sqlmodel.dataset_index_repository import (
    SqlModelDatasetIndexRepository,
)
from apps.semantic.services.dataset_index_service import (
    SemanticDatasetIndexService,
)


def test_rebuild_index_only_enqueues_unified_retrieval():
    dataset = SemanticDataset(
        id=9,
        oid=1,
        domain_id=2,
        name="客户数据集",
        biz_name="customer_dataset",
        index_version=2,
    )
    session = _DatasetSession(dataset)

    class _IndexGateway:
        def enqueue_dataset_rebuild(self, *, tenant_id: int, version, schema):
            assert tenant_id == 1
            assert version.dataset_id == 9
            assert version.schema_version == 1
            assert version.index_version == 3
            assert schema.data_set.id == 9
            return DatasetIndexEnqueueResult(
                source_id=77,
                generation="dataset-9-index-3",
                job_ids=(101, 102),
            )

    result = SemanticDatasetIndexService(
        SqlModelDatasetIndexRepository(session),
        _SchemaReader(),
        _IndexGateway(),
    ).rebuild_index(1, 9)

    assert dataset.index_version == 3
    assert session.added == [dataset]
    assert session.commit_count == 1
    assert result == DatasetIndexRebuildResult(
        dataset_id=9,
        index_version=3,
        source_id=77,
        generation="dataset-9-index-3",
        job_ids=(101, 102),
    )


@pytest.mark.anyio
async def test_rebuild_dataset_index_schedules_application_job_ids(monkeypatch):
    background_tasks = BackgroundTasks()

    class _IndexService:
        def __init__(self, *_dependencies):
            pass

        def rebuild_index(self, oid: int, dataset_id: int):
            assert oid == 1
            assert dataset_id == 9
            return DatasetIndexRebuildResult(
                dataset_id=9,
                index_version=3,
                source_id=77,
                generation="dataset-9-index-3",
                job_ids=(101, 102),
            )

    monkeypatch.setattr(
        dataset_indexes,
        "SemanticDatasetIndexService",
        _IndexService,
    )

    response = await dataset_indexes.rebuild_dataset_index(
        object(),
        SimpleNamespace(oid=1),
        background_tasks,
        dataset_id=9,
    )

    assert response["retrieval_source_id"] == 77
    assert response["retrieval_status"] == "queued"
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is process_semantic_index_jobs
    assert task.args == ((101, 102),)


class _DatasetSession:
    def __init__(self, dataset):
        self.dataset = dataset
        self.added = []
        self.commit_count = 0

    def get(self, _model, entity_id):
        return self.dataset if entity_id == self.dataset.id else None

    def add(self, entity):
        self.added.append(entity)

    def commit(self):
        self.commit_count += 1


class _SchemaReader:
    def build_dataset_schema(self, oid: int, dataset_id: int):
        assert oid == 1
        assert dataset_id == 9
        return SimpleNamespace(data_set=SimpleNamespace(id=9))
