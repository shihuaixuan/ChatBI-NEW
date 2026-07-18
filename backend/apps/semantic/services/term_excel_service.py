from __future__ import annotations

import re

from apps.semantic.errors import SemanticError, SemanticValidationError
from apps.semantic.models.dto import (
    TermPayload,
    TermWorkbookImportResult,
    TermWorkbookRow,
)
from apps.semantic.repository.term_workbook_repository import (
    TermWorkbookRepository,
)
from apps.semantic.services.term_service import SemanticTermService
from apps.semantic.utils.text import unique_texts

_LIST_SEPARATOR = re.compile(r"[,，;；\n]")
_ENABLED_VALUES = {"1", "Y", "YES", "TRUE"}
_DISABLED_VALUES = {"0", "N", "NO", "FALSE"}


class SemanticTermExcelService:
    """Semantic 术语 Excel 导入、导出和模板服务。"""

    def __init__(
        self,
        term_service: SemanticTermService,
        workbook_repository: TermWorkbookRepository,
    ) -> None:
        self._term_service = term_service
        self._workbook_repository = workbook_repository

    def export_workbook(
        self,
        oid: int,
        *,
        domain_id: int | None = None,
        word: str | None = None,
        dataset_ids: list[int] | None = None,
    ) -> bytes:
        terms = self._term_service.search_terms(
            oid,
            domain_id=domain_id,
            word=word,
            dataset_ids=dataset_ids,
        )
        rows = [
            TermWorkbookRow(
                row_number=index + 2,
                domain_id=str(term.domain_id),
                name=term.name,
                aliases=",".join(term.alias),
                description=term.description or "",
                dataset_ids=",".join(
                    str(dataset_id) for dataset_id in term.related_datasets
                ),
                enabled="Y" if term.status == 1 else "N",
            )
            for index, term in enumerate(terms)
        ]
        return self._workbook_repository.write(rows)

    def template_workbook(self) -> bytes:
        return self._workbook_repository.write(
            [
                TermWorkbookRow(
                    row_number=2,
                    domain_id="1",
                    name="人气",
                    aliases="热度,访问热度",
                    description="用于描述内容或商品的访问热度",
                    dataset_ids="10,11",
                    enabled="Y",
                ),
                TermWorkbookRow(
                    row_number=3,
                    domain_id="1",
                    name="通用口径",
                    aliases="统一口径",
                    description="dataset_ids 为空时作用于主题域内全部数据集",
                    dataset_ids="",
                    enabled="Y",
                ),
            ]
        )

    def import_workbook(
        self,
        oid: int,
        content: bytes,
    ) -> TermWorkbookImportResult:
        rows = self._workbook_repository.read(content)
        if not rows:
            raise SemanticValidationError("SEMANTIC_TERM_EXCEL_EMPTY")

        failed_rows: list[TermWorkbookRow] = []
        success_count = 0
        duplicate_count = 0
        for row in rows:
            try:
                payload, enabled = self._payload_from_row(row)
                self._term_service.create_term(
                    oid,
                    payload,
                    enabled=enabled,
                )
                success_count += 1
            except SemanticError as error:
                if error.detail == "SEMANTIC_TERM_NAME_EXISTS":
                    duplicate_count += 1
                failed_rows.append(row.model_copy(update={"error": error.detail}))

        error_workbook = (
            self._workbook_repository.write(
                failed_rows,
                include_error=True,
            )
            if failed_rows
            else None
        )
        return TermWorkbookImportResult(
            success_count=success_count,
            failed_count=len(failed_rows),
            duplicate_count=duplicate_count,
            original_count=len(rows),
            failed_rows=failed_rows,
            error_workbook=error_workbook,
        )

    def _payload_from_row(
        self,
        row: TermWorkbookRow,
    ) -> tuple[TermPayload, bool]:
        domain_id = self._positive_id(
            row.domain_id,
            "SEMANTIC_TERM_DOMAIN_REQUIRED",
        )
        name = row.name.strip()
        if not name:
            raise SemanticValidationError("SEMANTIC_TERM_NAME_EMPTY")
        return (
            TermPayload(
                domain_id=domain_id,
                name=name,
                alias=unique_texts(_LIST_SEPARATOR.split(row.aliases)),
                description=row.description.strip() or None,
                related_datasets=self._positive_ids(row.dataset_ids),
            ),
            self._enabled(row.enabled),
        )

    @staticmethod
    def _positive_ids(value: str) -> list[int]:
        if not value.strip():
            return []
        return [
            SemanticTermExcelService._positive_id(
                item,
                "SEMANTIC_TERM_REFERENCE_ID_INVALID",
            )
            for item in unique_texts(_LIST_SEPARATOR.split(value))
        ]

    @staticmethod
    def _positive_id(value: str, error_detail: str) -> int:
        normalized = value.strip()
        if not normalized.isdigit() or int(normalized) <= 0:
            raise SemanticValidationError(error_detail)
        return int(normalized)

    @staticmethod
    def _enabled(value: str) -> bool:
        normalized = value.strip().upper() or "Y"
        if normalized in _ENABLED_VALUES:
            return True
        if normalized in _DISABLED_VALUES:
            return False
        raise SemanticValidationError("SEMANTIC_TERM_ENABLED_INVALID")


__all__ = ["SemanticTermExcelService"]
