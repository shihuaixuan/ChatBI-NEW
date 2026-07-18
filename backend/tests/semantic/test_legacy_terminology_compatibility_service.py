import pytest

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import LegacyTerminologyDTO
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.services.term_compatibility_service import (
    LegacyTerminologyCompatibilityService,
)


def test_compatibility_create_maps_dataset_alias_and_assets():
    term_service = _TermService()
    service = LegacyTerminologyCompatibilityService(term_service)

    term_id = service.create_term(
        1,
        LegacyTerminologyDTO(
            domain_id=10,
            word="人气",
            other_words=["热度"],
            aliases=["访问热度"],
            specific_ds=True,
            dataset_ids=[20],
            mapped_assets=[
                {"assetType": "metric", "assetId": 100},
                {"type": "DIMENSION", "id": 200},
            ],
            enabled=False,
        ),
    )

    assert term_id == 7
    oid, payload, enabled = term_service.created
    assert oid == 1
    assert enabled is False
    assert payload.domain_id == 10
    assert payload.alias == ["热度", "访问热度"]
    assert payload.related_datasets == [20]
    assert payload.related_metrics == [100]
    assert payload.related_dimensions == [200]


def test_compatibility_rejects_legacy_datasource_scope_without_fallback():
    service = LegacyTerminologyCompatibilityService(_TermService())

    with pytest.raises(SemanticValidationError) as exc_info:
        service.create_term(
            1,
            LegacyTerminologyDTO(
                domain_id=10,
                word="人气",
                specific_ds=True,
                datasource_ids=[30],
            ),
        )

    assert exc_info.value.detail == "SEMANTIC_TERM_DATASOURCE_SCOPE_UNSUPPORTED"


def test_compatibility_page_preserves_disabled_status_and_dataset_filter():
    terms = [
        SemanticTerm(
            id=7,
            oid=1,
            domain_id=10,
            name="人气",
            alias=["热度"],
            related_datasets=[20],
            status=0,
        ),
        SemanticTerm(
            id=8,
            oid=1,
            domain_id=10,
            name="销售额",
            related_datasets=[21],
            status=1,
        ),
        SemanticTerm(
            id=9,
            oid=1,
            domain_id=10,
            name="通用口径",
            related_datasets=[],
            status=1,
        ),
    ]
    service = LegacyTerminologyCompatibilityService(_TermService(terms))

    page = service.page_terms(
        1,
        current_page=1,
        page_size=10,
        dataset_ids=[20],
    )

    assert page["total_count"] == 2
    assert [item.id for item in page["data"]] == [9, 7]
    assert page["data"][1].enabled is False
    assert page["data"][1].dataset_ids == [20]


class _TermService:
    def __init__(self, terms=None):
        self.terms = list(terms or [])
        self.created = None

    def list_terms(self, oid, domain_id=None):
        return [
            term
            for term in self.terms
            if term.oid == oid
            and (domain_id is None or term.domain_id == domain_id)
        ]

    def search_terms(
        self,
        oid,
        *,
        domain_id=None,
        word=None,
        dataset_ids=None,
    ):
        normalized_word = str(word or "").casefold()
        normalized_dataset_ids = set(dataset_ids or [])
        result = [
            term
            for term in self.list_terms(oid, domain_id)
            if (
                not normalized_word
                or normalized_word in term.name.casefold()
                or any(normalized_word in alias.casefold() for alias in term.alias)
            )
            and (
                not normalized_dataset_ids
                or not term.related_datasets
                or bool(normalized_dataset_ids.intersection(term.related_datasets))
            )
        ]
        return sorted(result, key=lambda term: term.id or 0, reverse=True)

    def create_term(self, oid, payload, *, enabled=True):
        self.created = (oid, payload, enabled)
        return SemanticTerm(
            id=7,
            oid=oid,
            domain_id=payload.domain_id,
            name=payload.name,
            status=1 if enabled else 0,
        )
