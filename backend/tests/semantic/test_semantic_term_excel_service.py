from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import TermWorkbookRow
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.repository.excel.term_workbook_repository import (
    ExcelTermWorkbookRepository,
)
from apps.semantic.services.term_excel_service import SemanticTermExcelService


def test_excel_repository_round_trip_preserves_stable_columns():
    repository = ExcelTermWorkbookRepository()
    content = repository.write(
        [
            TermWorkbookRow(
                row_number=2,
                domain_id="10",
                name="人气",
                aliases="热度,访问热度",
                description="访问热度",
                dataset_ids="20,21",
                enabled="N",
            )
        ]
    )

    rows = repository.read(content)

    assert len(rows) == 1
    assert rows[0].domain_id == "10"
    assert rows[0].name == "人气"
    assert rows[0].dataset_ids == "20,21"
    assert rows[0].enabled == "N"


def test_excel_service_imports_valid_rows_and_returns_error_workbook():
    repository = ExcelTermWorkbookRepository()
    term_service = _TermService()
    service = SemanticTermExcelService(term_service, repository)
    content = repository.write(
        [
            TermWorkbookRow(
                row_number=2,
                domain_id="10",
                name="人气",
                aliases="热度，访问热度",
                dataset_ids="20,21",
                enabled="N",
            ),
            TermWorkbookRow(
                row_number=3,
                domain_id="10",
                name="重复术语",
                enabled="Y",
            ),
            TermWorkbookRow(
                row_number=4,
                domain_id="",
                name="缺少主题域",
                enabled="Y",
            ),
        ]
    )

    result = service.import_workbook(1, content)

    assert result.success_count == 1
    assert result.failed_count == 2
    assert result.duplicate_count == 1
    assert result.original_count == 3
    assert result.error_workbook is not None
    oid, payload, enabled = term_service.created[0]
    assert oid == 1
    assert enabled is False
    assert payload.alias == ["热度", "访问热度"]
    assert payload.related_datasets == [20, 21]
    assert [row.error for row in result.failed_rows] == [
        "SEMANTIC_TERM_NAME_EXISTS",
        "SEMANTIC_TERM_DOMAIN_REQUIRED",
    ]


def test_excel_service_export_uses_semantic_scope_and_status():
    repository = ExcelTermWorkbookRepository()
    term_service = _TermService(
        terms=[
            SemanticTerm(
                id=7,
                oid=1,
                domain_id=10,
                name="人气",
                alias=["热度"],
                related_datasets=[20],
                status=0,
            )
        ]
    )
    service = SemanticTermExcelService(term_service, repository)

    rows = repository.read(
        service.export_workbook(1, domain_id=10, dataset_ids=[20])
    )

    assert len(rows) == 1
    assert rows[0].domain_id == "10"
    assert rows[0].dataset_ids == "20"
    assert rows[0].enabled == "N"


class _TermService:
    def __init__(self, terms=None):
        self.terms = list(terms or [])
        self.created = []

    def create_term(self, oid, payload, *, enabled=True):
        if payload.name == "重复术语":
            raise SemanticValidationError("SEMANTIC_TERM_NAME_EXISTS")
        self.created.append((oid, payload, enabled))
        return SemanticTerm(
            id=len(self.created),
            oid=oid,
            domain_id=payload.domain_id,
            name=payload.name,
            status=1 if enabled else 0,
        )

    def search_terms(
        self,
        oid,
        *,
        domain_id=None,
        word=None,
        dataset_ids=None,
    ):
        return [
            term
            for term in self.terms
            if term.oid == oid
            and (domain_id is None or term.domain_id == domain_id)
            and (
                not dataset_ids
                or not term.related_datasets
                or bool(set(dataset_ids).intersection(term.related_datasets))
            )
            and (not word or str(word).casefold() in term.name.casefold())
        ]
