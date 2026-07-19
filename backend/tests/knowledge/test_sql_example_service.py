from datetime import datetime

import pytest

from apps.knowledge.errors import (
    SQLExampleDuplicateError,
    SQLExampleError,
    SQLExampleNotFoundError,
)
from apps.knowledge.models.dto import (
    SQLExampleDatasetScope,
    SQLExampleIndexEnqueueResult,
    SQLExampleInput,
    SQLExampleMatch,
    SQLExampleRecord,
    SQLExampleSourceSnapshot,
    SQLExampleVerificationStatus,
)
from apps.knowledge.services import SQLExampleService


class RecordingSQLExampleRepository:
    def __init__(self) -> None:
        self.records: dict[int, SQLExampleRecord] = {}
        self.duplicate = False
        self.deleted: tuple[int, list[int]] | None = None
        self.enabled: tuple[int, int, bool] | None = None
        self.created: list[SQLExampleRecord] = []
        self.commit_count = 0

    def count(self, workspace_id: int, keyword: str | None = None) -> int:
        return len(self.list_by_workspace(workspace_id, keyword))

    def list_by_workspace(
        self,
        workspace_id: int,
        keyword: str | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[SQLExampleRecord]:
        rows = [item for item in self.records.values() if item.oid == workspace_id]
        if keyword:
            rows = [item for item in rows if keyword in item.question]
        return rows[offset : offset + limit if limit is not None else None]

    def get(self, workspace_id: int, example_id: int) -> SQLExampleRecord | None:
        record = self.records.get(example_id)
        return record if record is not None and record.oid == workspace_id else None

    def duplicate_exists(
        self,
        workspace_id: int,
        question: str,
        datasource_id: int | None,
        assistant_id: int | None,
        *,
        exclude_id: int | None = None,
    ) -> bool:
        _ = workspace_id, question, datasource_id, assistant_id, exclude_id
        return self.duplicate

    def create(self, example: SQLExampleRecord) -> int:
        example_id = 100 + len(self.created)
        created = example.model_copy(update={"id": example_id})
        self.records[example_id] = created
        self.created.append(created)
        return example_id

    def update(self, example: SQLExampleRecord) -> int:
        assert example.id is not None
        self.records[example.id] = example
        return example.id

    def delete(self, workspace_id: int, example_ids: list[int]) -> None:
        self.deleted = (workspace_id, example_ids)
        for example_id in example_ids:
            record = self.records.get(example_id)
            if record is not None and record.oid == workspace_id:
                del self.records[example_id]

    def set_enabled(
        self,
        workspace_id: int,
        example_id: int,
        enabled: bool,
    ) -> bool:
        self.enabled = (workspace_id, example_id, enabled)
        record = self.get(workspace_id, example_id)
        if record is None:
            return False
        self.records[example_id] = record.model_copy(update={"enabled": enabled})
        return True

    def commit(self) -> None:
        self.commit_count += 1

    def search_lexical_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        _ = workspace_id, question, datasource_id, assistant_id
        return []

    def get_matches(
        self,
        workspace_id: int,
        example_ids: list[int],
    ) -> list[SQLExampleMatch]:
        _ = workspace_id, example_ids
        return []


class FakeReferenceCatalog:
    def __init__(self) -> None:
        self.dataset_scopes = {
            30: SQLExampleDatasetScope(
                dataset_id=30,
                datasource_ids=(8,),
                metric_ids=(100,),
                dimension_ids=(200,),
            )
        }

    def datasource_names(
        self,
        workspace_id: int,
        datasource_ids: list[int] | None = None,
    ) -> dict[int, str]:
        _ = workspace_id
        values = {8: "销售库", 9: "库存库"}
        if datasource_ids is None:
            return values
        return {key: values[key] for key in datasource_ids if key in values}

    def assistant_names(
        self,
        workspace_id: int,
        assistant_ids: list[int] | None = None,
    ) -> dict[int, str]:
        _ = workspace_id
        values = {20: "销售助手"}
        if assistant_ids is None:
            return values
        return {key: values[key] for key in assistant_ids if key in values}

    def dataset_scope(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SQLExampleDatasetScope | None:
        _ = workspace_id
        return self.dataset_scopes.get(dataset_id)


class RecordingIndexGateway:
    def __init__(self) -> None:
        self.snapshots: list[SQLExampleSourceSnapshot] = []
        self.submissions: list[tuple[int, ...]] = []

    def stage_rebuild(
        self,
        snapshot: SQLExampleSourceSnapshot,
    ) -> SQLExampleIndexEnqueueResult:
        self.snapshots.append(snapshot)
        return SQLExampleIndexEnqueueResult(
            source_id=1,
            generation=f"generation-{len(self.snapshots)}",
            job_ids=(10 + len(self.snapshots),),
        )

    def submit(self, job_ids: tuple[int, ...]) -> None:
        self.submissions.append(job_ids)


class FailingIndexGateway(RecordingIndexGateway):
    def stage_rebuild(
        self,
        snapshot: SQLExampleSourceSnapshot,
    ) -> SQLExampleIndexEnqueueResult:
        self.snapshots.append(snapshot)
        raise RuntimeError("索引任务暂存失败")


def _service(
    repository: RecordingSQLExampleRepository,
    index_gateway: RecordingIndexGateway | None = None,
) -> SQLExampleService:
    return SQLExampleService(
        repository,
        FakeReferenceCatalog(),
        index_gateway or RecordingIndexGateway(),
    )


def test_create_normalizes_content_and_owns_workspace_fields():
    repository = RecordingSQLExampleRepository()
    index_gateway = RecordingIndexGateway()
    service = _service(repository, index_gateway)

    example_id = service.create(
        3,
        SQLExampleInput(
            oid=99,
            question="  本月销售额  ",
            description="  SELECT SUM(amount) FROM orders  ",
            datasource=8,
        ),
    )

    created = repository.created[0]
    assert example_id == 100
    assert created.oid == 3
    assert created.question == "本月销售额"
    assert created.description == "SELECT SUM(amount) FROM orders"
    assert created.create_time is not None
    assert created.verification_status == SQLExampleVerificationStatus.VERIFIED
    assert [item.id for item in index_gateway.snapshots[0].examples] == [100]
    assert index_gateway.submissions == [(11,)]
    assert repository.commit_count == 1


def test_create_normalizes_and_validates_dataset_linked_assets():
    repository = RecordingSQLExampleRepository()
    service = _service(repository)

    service.create(
        3,
        SQLExampleInput(
            question="本月销售额",
            description="SELECT 1",
            datasource=8,
            dataset_id=30,
            linked_assets=[
                {"type": "metric", "id": 100},
                {"assetType": "METRIC", "assetId": 100},
                {"asset_type": "DIMENSION", "asset_id": 200},
            ],
        ),
    )

    created = repository.created[0]
    assert created.linked_assets == [
        {"asset_type": "METRIC", "asset_id": 100},
        {"asset_type": "DIMENSION", "asset_id": 200},
    ]


@pytest.mark.parametrize(
    ("payload", "message_key"),
    [
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                datasource=99,
            ),
            "i18n_data_training.datasource_not_found",
        ),
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                advanced_application=99,
            ),
            "i18n_data_training.advanced_application_not_found",
        ),
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                dataset_id=99,
            ),
            "i18n_data_training.dataset_not_found",
        ),
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                datasource=9,
                dataset_id=30,
            ),
            "i18n_data_training.dataset_datasource_mismatch",
        ),
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                datasource=8,
                linked_assets=[{"asset_type": "METRIC", "asset_id": 100}],
            ),
            "i18n_data_training.linked_assets_require_dataset",
        ),
        (
            SQLExampleInput(
                question="销售额",
                description="SELECT 1",
                dataset_id=30,
                linked_assets=[{"asset_type": "METRIC", "asset_id": 999}],
            ),
            "i18n_data_training.linked_asset_not_found",
        ),
    ],
)
def test_create_rejects_invalid_cross_domain_references(payload, message_key):
    repository = RecordingSQLExampleRepository()

    with pytest.raises(SQLExampleError) as exc_info:
        _service(repository).create(3, payload)

    assert exc_info.value.message_key == message_key
    assert repository.created == []


def test_duplicate_is_rejected_before_create_and_index():
    repository = RecordingSQLExampleRepository()
    repository.duplicate = True
    index_gateway = RecordingIndexGateway()
    service = _service(repository, index_gateway)

    with pytest.raises(SQLExampleDuplicateError):
        service.create(
            3,
            SQLExampleInput(
                question="本月销售额",
                description="SELECT 1",
                datasource=8,
            ),
        )

    assert repository.created == []
    assert index_gateway.snapshots == []


def test_index_stage_failure_prevents_source_commit_and_legacy_vector_submission():
    repository = RecordingSQLExampleRepository()
    service = _service(repository, FailingIndexGateway())

    with pytest.raises(RuntimeError, match="索引任务暂存失败"):
        service.create(
            3,
            SQLExampleInput(
                question="本月销售额",
                description="SELECT 1",
                datasource=8,
            ),
        )

    assert repository.commit_count == 0


def test_page_resolves_reference_names_without_cross_domain_orm():
    repository = RecordingSQLExampleRepository()
    repository.records[10] = SQLExampleRecord(
        id=10,
        oid=3,
        datasource=8,
        advanced_application=20,
        create_time=datetime.now(),
        question="本月销售额",
        description="SELECT 1",
    )
    service = _service(repository)

    page = service.page(3, current_page=1, page_size=5)

    assert page.page_size == 10
    assert page.total_count == 1
    assert page.data[0].datasource_name == "销售库"
    assert page.data[0].advanced_application_name == "销售助手"


def test_update_cannot_modify_another_workspace_record():
    repository = RecordingSQLExampleRepository()
    repository.records[10] = SQLExampleRecord(
        id=10,
        oid=4,
        datasource=8,
        question="本月销售额",
        description="SELECT 1",
    )

    with pytest.raises(SQLExampleNotFoundError):
        _service(repository).update(
            3,
            SQLExampleInput(
                id=10,
                question="本月销售额",
                description="SELECT 2",
                datasource=8,
            ),
        )


def test_disabling_example_removes_it_from_complete_index_snapshot():
    repository = RecordingSQLExampleRepository()
    repository.records[10] = SQLExampleRecord(
        id=10,
        oid=3,
        datasource=8,
        question="本月销售额",
        description="SELECT 1",
    )
    index_gateway = RecordingIndexGateway()
    service = _service(repository, index_gateway)

    service.set_enabled(3, 10, False)

    assert index_gateway.snapshots[0].examples == ()
    assert repository.commit_count == 1


def test_delete_is_scoped_to_workspace_and_submits_index_deletion():
    repository = RecordingSQLExampleRepository()
    index_gateway = RecordingIndexGateway()
    service = _service(repository, index_gateway)

    service.delete(3, [2, 1, 2, -1])

    assert repository.deleted == (3, [1, 2])
    assert len(index_gateway.snapshots) == 1
    assert index_gateway.snapshots[0].examples == ()


def test_batch_import_resolves_names_deduplicates_and_indexes_once():
    repository = RecordingSQLExampleRepository()
    index_gateway = RecordingIndexGateway()
    service = _service(repository, index_gateway)
    valid = SQLExampleInput(
        question="本月销售额",
        description="SELECT 1",
        datasource_name="销售库",
    )

    result = service.batch_import(
        3,
        [
            valid,
            valid.model_copy(),
            SQLExampleInput(
                question="库存",
                description="SELECT 2",
                datasource_name="不存在的数据源",
            ),
        ],
    )

    assert result.success_count == 1
    assert result.duplicate_count == 1
    assert len(result.failed_records) == 1
    assert result.failed_records[0].errors[0].message_key.endswith(
        "datasource_not_found"
    )
    assert [item.id for item in index_gateway.snapshots[0].examples] == [100]
