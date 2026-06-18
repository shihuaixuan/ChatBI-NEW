from __future__ import annotations

import re

from pydantic import BaseModel, Field

from apps.semantic.assets.cache import RuntimeAssetCache, runtime_asset_cache
from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.enums import AssetEventType, AssetType
from apps.semantic.services.transaction_hooks import run_after_commit
from common.core.config import settings

CONNECTION_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
SENSITIVE_ASSIGNMENT = re.compile(r"\b(password|token|configuration)\s*=\s*\S+", re.IGNORECASE)


class AssetChangedEvent(BaseModel):
    event_type: AssetEventType
    asset_type: AssetType
    asset_id: int | str
    oid: int = 1
    datasource_id: int | None = None
    dataset_id: int | str | None = None
    change_fields: list[str] = Field(default_factory=list)


class AssetSyncReport(BaseModel):
    asset_type: AssetType
    asset_id: int | str
    dataset_id: int | str | None = None
    datasource_id: int | None = None
    document_rebuilt: bool = False
    embedding_rebuilt: bool = False
    keyword_index_updated: bool = False
    cache_invalidated: bool = False
    skipped: bool = False
    retryable: bool = False
    change_fields: list[str] = Field(default_factory=list)
    invalidated_cache_keys: int = 0
    errors: list[str] = Field(default_factory=list)


class AssetSyncBatchReport(BaseModel):
    oid: int
    datasource_ids: list[int] = Field(default_factory=list)
    items: list[AssetSyncReport] = Field(default_factory=list)
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class AssetIndexSyncService:
    def __init__(self, cache: RuntimeAssetCache | None = runtime_asset_cache):
        self.cache = cache

    def handle_event(self, event: AssetChangedEvent) -> AssetSyncReport:
        if not _event_matches_asset(event):
            return AssetSyncReport(
                asset_type=event.asset_type,
                asset_id=event.asset_id,
                dataset_id=event.dataset_id,
                datasource_id=event.datasource_id,
                skipped=True,
                change_fields=event.change_fields,
                errors=["UNSUPPORTED_ASSET_EVENT"],
            )
        dataset_id = self._resolve_dataset_id(event)
        search_text_fields = {
            "name",
            "display_name",
            "aliases",
            "description",
            "expr",
            "metric_group",
            "word",
            "question",
            "field_name",
            "field_comment",
            "custom_comment",
        }
        should_rebuild_embedding = not event.change_fields or bool(search_text_fields.intersection(event.change_fields))
        invalidated_cache_keys = self.cache.invalidate_dataset(dataset_id) if self.cache is not None else 0
        return AssetSyncReport(
            asset_type=event.asset_type,
            asset_id=event.asset_id,
            dataset_id=dataset_id,
            datasource_id=event.datasource_id,
            document_rebuilt=True,
            embedding_rebuilt=should_rebuild_embedding,
            keyword_index_updated=True,
            cache_invalidated=True,
            change_fields=event.change_fields,
            invalidated_cache_keys=invalidated_cache_keys,
        )

    def sync_dataset(self, scope: DatasetScope, dry_run: bool = False) -> AssetSyncReport:
        if dry_run:
            return AssetSyncReport(
                asset_type=AssetType.DATASET,
                asset_id=scope.dataset_id,
                dataset_id=scope.dataset_id,
                datasource_id=scope.datasource_id,
                skipped=True,
            )
        invalidated_cache_keys = self.cache.invalidate_dataset(scope.dataset_id) if self.cache is not None else 0
        return AssetSyncReport(
            asset_type=AssetType.DATASET,
            asset_id=scope.dataset_id,
            dataset_id=scope.dataset_id,
            datasource_id=scope.datasource_id,
            document_rebuilt=True,
            embedding_rebuilt=True,
            keyword_index_updated=True,
            cache_invalidated=True,
            invalidated_cache_keys=invalidated_cache_keys,
        )

    def sync_datasource(self, oid: int, datasource_id: int, table_ids: list[int] | None = None, dry_run: bool = False) -> AssetSyncReport:
        scope = DatasetScope.virtual(oid=oid, datasource_id=datasource_id, table_ids=table_ids or [])
        return self.sync_dataset(scope, dry_run=dry_run)

    def sync_workspace(
        self,
        oid: int,
        datasource_ids: list[int] | None = None,
        dry_run: bool = False,
    ) -> AssetSyncBatchReport:
        report = AssetSyncBatchReport(oid=oid, datasource_ids=datasource_ids or [])
        for datasource_id in datasource_ids or []:
            item = self.sync_datasource(oid=oid, datasource_id=datasource_id, dry_run=dry_run)
            report.items.append(item)
        _refresh_batch_counts(report)
        return report

    def sync_events(self, events: list[AssetChangedEvent]) -> AssetSyncBatchReport:
        oid = events[0].oid if events else 1
        report = AssetSyncBatchReport(
            oid=oid,
            datasource_ids=_unique_datasource_ids(events),
        )
        for event in events:
            try:
                item = self.handle_event(event)
            except Exception as exc:
                item = self.mark_failed(event, exc)
            report.items.append(item)
        _refresh_batch_counts(report)
        return report

    def mark_failed(self, event: AssetChangedEvent, exc: Exception) -> AssetSyncReport:
        return AssetSyncReport(
            asset_type=event.asset_type,
            asset_id=event.asset_id,
            dataset_id=self._resolve_dataset_id(event),
            datasource_id=event.datasource_id,
            retryable=True,
            change_fields=event.change_fields,
            errors=[_sanitize_error_detail(str(exc))],
        )

    def _resolve_dataset_id(self, event: AssetChangedEvent) -> int | str | None:
        if event.dataset_id is not None:
            return event.dataset_id
        if event.datasource_id is None:
            return None
        return DatasetScope.virtual(oid=event.oid, datasource_id=event.datasource_id).dataset_id


def schedule_asset_index_sync(session, event: AssetChangedEvent) -> None:
    if not settings.SEMANTIC_ASSET_INDEX_SYNC_ENABLED:
        return

    def _callback() -> None:
        service = AssetIndexSyncService()
        try:
            report = service.handle_event(event)
        except Exception as exc:
            report = service.mark_failed(event, exc)
        _append_sync_report(session, report)

    run_after_commit(session, _callback)


def _append_sync_report(session, report: AssetSyncReport) -> None:
    reports = session.info.setdefault("semantic_asset_sync_reports", [])
    reports.append(report)


def _event_matches_asset(event: AssetChangedEvent) -> bool:
    mapping = {
        AssetEventType.MetricChanged: {AssetType.METRIC},
        AssetEventType.DimensionChanged: {AssetType.DIMENSION},
        AssetEventType.DimensionValueChanged: {AssetType.DIMENSION_VALUE},
        AssetEventType.TerminologyChanged: {AssetType.TERM},
        AssetEventType.DataTrainingChanged: {AssetType.EXAMPLE},
        AssetEventType.DatasetChanged: {AssetType.DATASET},
        AssetEventType.SchemaChanged: {AssetType.FIELD, AssetType.DATASET},
    }
    return event.asset_type in mapping.get(event.event_type, set())


def _refresh_batch_counts(report: AssetSyncBatchReport) -> None:
    report.success = sum(1 for item in report.items if not item.skipped and not item.errors)
    report.failed = sum(1 for item in report.items if item.errors and not item.skipped)
    report.skipped = sum(1 for item in report.items if item.skipped)
    report.errors = [error for item in report.items for error in item.errors]


def _unique_datasource_ids(events: list[AssetChangedEvent]) -> list[int]:
    result: list[int] = []
    for event in events:
        if event.datasource_id is None or event.datasource_id in result:
            continue
        result.append(event.datasource_id)
    return result


def _sanitize_error_detail(message: str) -> str:
    message = CONNECTION_URL.sub("[REDACTED_DSN]", message)
    return SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)
