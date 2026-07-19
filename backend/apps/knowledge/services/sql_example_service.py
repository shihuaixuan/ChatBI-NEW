from datetime import datetime
from typing import Any

from apps.knowledge.errors import (
    SQLExampleDuplicateError,
    SQLExampleError,
    SQLExampleNotFoundError,
)
from apps.knowledge.models.dto import (
    SQLExampleErrorDetail,
    SQLExampleImportFailure,
    SQLExampleImportResult,
    SQLExampleInput,
    SQLExamplePage,
    SQLExampleRecord,
    SQLExampleResult,
    SQLExampleSourceSnapshot,
    SQLExampleVerificationStatus,
)
from apps.knowledge.repository import (
    SQLExampleIndexGateway,
    SQLExampleReferenceCatalog,
    SQLExampleRepository,
)


class SQLExampleService:
    """SQL 示例源数据维护的唯一业务入口。"""

    def __init__(
        self,
        repository: SQLExampleRepository,
        reference_catalog: SQLExampleReferenceCatalog,
        index_gateway: SQLExampleIndexGateway,
    ) -> None:
        self._repository = repository
        self._reference_catalog = reference_catalog
        self._index_gateway = index_gateway

    def page(
        self,
        workspace_id: int,
        current_page: int = 1,
        page_size: int = 10,
        keyword: str | None = None,
    ) -> SQLExamplePage:
        resolved_page_size = max(10, page_size)
        total_count = self._repository.count(workspace_id, keyword)
        total_pages = (
            (total_count + resolved_page_size - 1) // resolved_page_size
            if total_count > 0
            else 0
        )
        resolved_page = (
            max(1, min(current_page, total_pages)) if total_pages > 0 else 1
        )
        records = self._repository.list_by_workspace(
            workspace_id,
            keyword,
            offset=(resolved_page - 1) * resolved_page_size,
            limit=resolved_page_size,
        )
        return SQLExamplePage(
            current_page=resolved_page,
            page_size=resolved_page_size,
            total_count=total_count,
            total_pages=total_pages,
            data=self._with_reference_names(workspace_id, records),
        )

    def list_all(
        self,
        workspace_id: int,
        keyword: str | None = None,
    ) -> list[SQLExampleResult]:
        records = self._repository.list_by_workspace(workspace_id, keyword)
        return self._with_reference_names(workspace_id, records)

    def create(self, workspace_id: int, request: SQLExampleInput) -> int:
        example_id = self._create(workspace_id, request)
        self._commit_index_change(workspace_id)
        return example_id

    def update(self, workspace_id: int, request: SQLExampleInput) -> int:
        if request.id is None:
            raise SQLExampleNotFoundError()
        current = self._repository.get(workspace_id, request.id)
        if current is None:
            raise SQLExampleNotFoundError()
        example = self._validated_record(
            workspace_id,
            request,
            example_id=request.id,
            create_time=current.create_time,
        )
        self._ensure_not_duplicate(example, exclude_id=request.id)
        example_id = self._repository.update(example)
        self._commit_index_change(workspace_id)
        return example_id

    def delete(self, workspace_id: int, example_ids: list[int]) -> None:
        normalized_ids = sorted({value for value in example_ids if value > 0})
        if not normalized_ids:
            return
        self._repository.delete(workspace_id, normalized_ids)
        self._commit_index_change(workspace_id)

    def set_enabled(
        self,
        workspace_id: int,
        example_id: int,
        enabled: bool,
    ) -> None:
        if not self._repository.set_enabled(workspace_id, example_id, enabled):
            raise SQLExampleNotFoundError()
        self._commit_index_change(workspace_id)

    def batch_import(
        self,
        workspace_id: int,
        requests: list[SQLExampleInput],
    ) -> SQLExampleImportResult:
        unique_requests: dict[tuple[str, str, str], SQLExampleInput] = {}
        duplicate_count = 0
        for request in requests:
            key = (
                (request.question or "").strip().lower(),
                (request.datasource_name or "").strip().lower(),
                (request.advanced_application_name or "").strip().lower(),
            )
            if key in unique_requests:
                duplicate_count += 1
            else:
                unique_requests[key] = request

        datasource_names = self._reference_catalog.datasource_names(workspace_id)
        datasource_ids = {name.strip(): item_id for item_id, name in datasource_names.items()}
        assistant_names = self._reference_catalog.assistant_names(workspace_id)
        assistant_ids = {name.strip(): item_id for item_id, name in assistant_names.items()}

        inserted_ids: list[int] = []
        failures: list[SQLExampleImportFailure] = []
        for request in unique_requests.values():
            normalized, errors = self._resolve_import_references(
                request,
                datasource_ids,
                assistant_ids,
            )
            if errors:
                failures.append(SQLExampleImportFailure(data=request, errors=errors))
                continue
            try:
                inserted_ids.append(self._create(workspace_id, normalized))
            except SQLExampleError as exc:
                failures.append(
                    SQLExampleImportFailure(
                        data=request,
                        errors=[self._error_detail(exc)],
                    )
                )

        if inserted_ids:
            self._commit_index_change(workspace_id)
        return SQLExampleImportResult(
            success_count=len(inserted_ids),
            failed_records=failures,
            duplicate_count=duplicate_count,
            original_count=len(requests),
            deduplicated_count=len(unique_requests),
        )

    def _create(self, workspace_id: int, request: SQLExampleInput) -> int:
        example = self._validated_record(workspace_id, request)
        self._ensure_not_duplicate(example)
        return self._repository.create(example)

    def _commit_index_change(self, workspace_id: int) -> None:
        """在同一事务内提交源快照和 durable job，提交后再启动后台消费者。"""

        records = self._repository.list_by_workspace(workspace_id)
        snapshot = SQLExampleSourceSnapshot.from_records(workspace_id, records)
        enqueue_result = self._index_gateway.stage_rebuild(snapshot)
        self._repository.commit()
        self._index_gateway.submit(enqueue_result.job_ids)

    def _validated_record(
        self,
        workspace_id: int,
        request: SQLExampleInput,
        *,
        example_id: int | None = None,
        create_time: datetime | None = None,
    ) -> SQLExampleRecord:
        question = (request.question or "").strip()
        if not question:
            raise SQLExampleError("i18n_data_training.question_cannot_be_empty")
        description = (request.description or "").strip()
        if not description:
            raise SQLExampleError("i18n_data_training.description_cannot_be_empty")
        if all(
            value is None
            for value in (
                request.datasource,
                request.advanced_application,
                request.dataset_id,
            )
        ):
            raise SQLExampleError("i18n_data_training.reference_cannot_be_none")
        linked_assets = self._validated_references(workspace_id, request)
        return SQLExampleRecord(
            id=example_id,
            oid=workspace_id,
            datasource=request.datasource,
            create_time=create_time or datetime.now(),
            question=question,
            description=description,
            example_type=request.example_type or "QUESTION_EXAMPLE",
            sql=request.sql,
            linked_assets=linked_assets,
            dataset_id=request.dataset_id,
            enabled=request.enabled if request.enabled is not None else True,
            advanced_application=request.advanced_application,
            verification_status=SQLExampleVerificationStatus.VERIFIED,
        )

    def _validated_references(
        self,
        workspace_id: int,
        request: SQLExampleInput,
    ) -> list[dict[str, Any]]:
        """统一校验数据源、助手、数据集及数据集内的关联资产。"""

        if request.datasource is not None:
            datasource_names = self._reference_catalog.datasource_names(
                workspace_id,
                [request.datasource],
            )
            if request.datasource not in datasource_names:
                raise SQLExampleError(
                    "i18n_data_training.datasource_not_found",
                    request.datasource,
                )
        if request.advanced_application is not None:
            assistant_names = self._reference_catalog.assistant_names(
                workspace_id,
                [request.advanced_application],
            )
            if request.advanced_application not in assistant_names:
                raise SQLExampleError(
                    "i18n_data_training.advanced_application_not_found",
                    request.advanced_application,
                )

        assets_by_key = {
            (asset.asset_type, asset.asset_id): asset
            for asset in request.linked_assets or []
        }
        linked_assets = list(assets_by_key.values())
        if request.dataset_id is None:
            if linked_assets:
                raise SQLExampleError(
                    "i18n_data_training.linked_assets_require_dataset"
                )
            return []

        dataset_scope = self._reference_catalog.dataset_scope(
            workspace_id,
            request.dataset_id,
        )
        if dataset_scope is None:
            raise SQLExampleError(
                "i18n_data_training.dataset_not_found",
                request.dataset_id,
            )
        if (
            request.datasource is not None
            and request.datasource not in dataset_scope.datasource_ids
        ):
            raise SQLExampleError(
                "i18n_data_training.dataset_datasource_mismatch",
                request.dataset_id,
                request.datasource,
            )

        valid_asset_ids = {
            "METRIC": set(dataset_scope.metric_ids),
            "DIMENSION": set(dataset_scope.dimension_ids),
        }
        for asset in linked_assets:
            if asset.asset_id not in valid_asset_ids[asset.asset_type]:
                raise SQLExampleError(
                    "i18n_data_training.linked_asset_not_found",
                    asset.asset_type,
                    asset.asset_id,
                )
        return [asset.model_dump(mode="json") for asset in linked_assets]

    def _ensure_not_duplicate(
        self,
        example: SQLExampleRecord,
        *,
        exclude_id: int | None = None,
    ) -> None:
        if self._repository.duplicate_exists(
            example.oid,
            example.question,
            example.datasource,
            example.advanced_application,
            exclude_id=exclude_id,
        ):
            raise SQLExampleDuplicateError()

    def _with_reference_names(
        self,
        workspace_id: int,
        records: list[SQLExampleRecord],
    ) -> list[SQLExampleResult]:
        datasource_ids = sorted(
            {item.datasource for item in records if item.datasource is not None}
        )
        assistant_ids = sorted(
            {
                item.advanced_application
                for item in records
                if item.advanced_application is not None
            }
        )
        datasource_names = self._reference_catalog.datasource_names(
            workspace_id,
            datasource_ids,
        )
        assistant_names = self._reference_catalog.assistant_names(
            workspace_id,
            assistant_ids,
        )
        return [
            SQLExampleResult(
                id=str(item.id) if item.id is not None else None,
                oid=str(item.oid),
                datasource=item.datasource,
                datasource_name=(
                    datasource_names.get(item.datasource)
                    if item.datasource is not None
                    else None
                ),
                create_time=item.create_time,
                question=item.question,
                description=item.description,
                example_type=item.example_type,
                sql=item.sql,
                linked_assets=item.linked_assets,
                dataset_id=item.dataset_id,
                enabled=item.enabled,
                verification_status=item.verification_status,
                advanced_application=(
                    str(item.advanced_application)
                    if item.advanced_application is not None
                    else None
                ),
                advanced_application_name=(
                    assistant_names.get(item.advanced_application)
                    if item.advanced_application is not None
                    else None
                ),
            )
            for item in records
        ]

    def _resolve_import_references(
        self,
        request: SQLExampleInput,
        datasource_ids: dict[str, int],
        assistant_ids: dict[str, int],
    ) -> tuple[SQLExampleInput, list[SQLExampleErrorDetail]]:
        errors: list[SQLExampleErrorDetail] = []
        datasource_id: int | None = None
        datasource_name = (request.datasource_name or "").strip()
        if datasource_name:
            datasource_id = datasource_ids.get(datasource_name)
            if datasource_id is None:
                errors.append(
                    SQLExampleErrorDetail(
                        message_key="i18n_data_training.datasource_not_found",
                        format_args=[datasource_name],
                    )
                )

        assistant_id: int | None = None
        assistant_name = (request.advanced_application_name or "").strip()
        if assistant_name:
            assistant_id = assistant_ids.get(assistant_name)
            if assistant_id is None:
                errors.append(
                    SQLExampleErrorDetail(
                        message_key=(
                            "i18n_data_training.advanced_application_not_found"
                        ),
                        format_args=[assistant_name],
                    )
                )
        normalized = request.model_copy(
            update={
                "datasource": datasource_id,
                "advanced_application": assistant_id,
            }
        )
        return normalized, errors

    @staticmethod
    def _error_detail(error: SQLExampleError) -> SQLExampleErrorDetail:
        return SQLExampleErrorDetail(
            message_key=error.message_key,
            format_args=[str(value) for value in error.format_args],
        )
