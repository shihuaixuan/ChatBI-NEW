from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import TermPayload
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.term_repository import TermRepository
from apps.semantic.utils.model_update import assign_values
from apps.semantic.utils.text import unique_texts


class SemanticTermService:
    """术语管理的应用服务。"""

    def __init__(
        self,
        repository: TermRepository,
        domain_repository: DomainRepository,
    ):
        self._repository = repository
        self._domain_repository = domain_repository

    def list_terms(
        self, oid: int, domain_id: int | None = None
    ) -> list[SemanticTerm]:
        return self._repository.list_all(oid, domain_id)

    def search_terms(
        self,
        oid: int,
        *,
        domain_id: int | None = None,
        word: str | None = None,
        dataset_ids: list[int] | None = None,
    ) -> list[SemanticTerm]:
        normalized_word = str(word or "").strip().casefold()
        normalized_dataset_ids = set(self._reference_ids(dataset_ids or []))
        terms = self._repository.list_all(oid, domain_id)
        result = [
            term
            for term in terms
            if (
                not normalized_word
                or normalized_word in term.name.casefold()
                or any(
                    normalized_word in alias.casefold()
                    for alias in term.alias
                )
            )
            and (
                not normalized_dataset_ids
                or not term.related_datasets
                or bool(
                    normalized_dataset_ids.intersection(term.related_datasets)
                )
            )
        ]
        return sorted(result, key=lambda term: term.id or 0, reverse=True)

    def create_term(
        self,
        oid: int,
        payload: TermPayload,
        *,
        enabled: bool = True,
    ) -> SemanticTerm:
        payload = self._validated_payload(oid, payload)
        self._require_unique_name(oid, payload.domain_id, payload.name)
        term = SemanticTerm(
            **payload.model_dump(),
            oid=oid,
            status=1 if enabled else 0,
        )
        return self._repository.create(term)

    def update_term(
        self,
        oid: int,
        term_id: int,
        payload: TermPayload,
        *,
        enabled: bool | None = None,
    ) -> SemanticTerm:
        term = self._require_term(oid, term_id)
        payload = self._validated_payload(oid, payload)
        self._require_unique_name(
            oid,
            payload.domain_id,
            payload.name,
            exclude_id=term_id,
        )
        assign_values(term, payload.model_dump())
        if enabled is not None:
            term.status = 1 if enabled else 0
        return self._repository.update(term)

    def delete_term(self, oid: int, term_id: int) -> dict[str, int | bool]:
        term = self._require_term(oid, term_id)
        self._repository.delete(term)
        return {"id": term_id, "deleted": True}

    def delete_terms(self, oid: int, term_ids: list[int]) -> dict[str, list[int]]:
        normalized_ids = self._reference_ids(term_ids)
        terms = [self._require_term(oid, term_id) for term_id in normalized_ids]
        self._repository.delete_many(terms)
        return {"deleted_ids": normalized_ids}

    def set_term_enabled(
        self,
        oid: int,
        term_id: int,
        enabled: bool,
    ) -> SemanticTerm:
        term = self._require_term(oid, term_id)
        term.status = 1 if enabled else 0
        return self._repository.update(term)

    def _require_term(self, oid: int, term_id: int) -> SemanticTerm:
        term = self._repository.get(oid, term_id)
        if term is None:
            raise SemanticNotFoundError("SEMANTIC_TERM_NOT_FOUND")
        return term

    def _require_domain(self, oid: int, domain_id: int) -> None:
        if not self._domain_repository.is_active(oid, domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")

    def _validated_payload(self, oid: int, payload: TermPayload) -> TermPayload:
        self._require_domain(oid, payload.domain_id)
        name = payload.name.strip()
        if not name:
            raise SemanticValidationError("SEMANTIC_TERM_NAME_EMPTY")

        values = payload.model_dump()
        values["name"] = name
        values["alias"] = [
            alias
            for alias in unique_texts(payload.alias)
            if alias != name
        ]
        for field_name in (
            "related_datasets",
            "related_metrics",
            "related_dimensions",
        ):
            values[field_name] = self._reference_ids(values[field_name])

        normalized = TermPayload(**values)
        validation = self._repository.validate_references(
            oid,
            normalized.domain_id,
            normalized.related_datasets,
            normalized.related_metrics,
            normalized.related_dimensions,
        )
        if validation.invalid_dataset_ids:
            raise SemanticValidationError("SEMANTIC_TERM_DATASET_REFERENCE_INVALID")
        if validation.invalid_metric_ids:
            raise SemanticValidationError("SEMANTIC_TERM_METRIC_REFERENCE_INVALID")
        if validation.invalid_dimension_ids:
            raise SemanticValidationError("SEMANTIC_TERM_DIMENSION_REFERENCE_INVALID")
        return normalized

    def _require_unique_name(
        self,
        oid: int,
        domain_id: int,
        name: str,
        exclude_id: int | None = None,
    ) -> None:
        if self._repository.name_exists(
            oid,
            domain_id,
            name,
            exclude_id=exclude_id,
        ):
            raise SemanticValidationError("SEMANTIC_TERM_NAME_EXISTS")

    @staticmethod
    def _reference_ids(values: list[int]) -> list[int]:
        result: list[int] = []
        for value in values:
            if isinstance(value, bool) or value <= 0:
                raise SemanticValidationError("SEMANTIC_TERM_REFERENCE_ID_INVALID")
            if value not in result:
                result.append(value)
        return result
