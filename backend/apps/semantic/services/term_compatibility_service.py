"""旧术语管理接口到 Semantic 术语服务的兼容转换。"""

from __future__ import annotations

from typing import Any

from apps.semantic.errors import SemanticDataAccessError, SemanticValidationError
from apps.semantic.models.dto import LegacyTerminologyDTO, TermPayload
from apps.semantic.models.orm import SemanticTerm
from apps.semantic.services.term_service import SemanticTermService


class LegacyTerminologyCompatibilityService:
    """保留旧 HTTP 契约，但只调用 Semantic 的权威写入服务。"""

    def __init__(self, term_service: SemanticTermService) -> None:
        self._term_service = term_service

    def page_terms(
        self,
        oid: int,
        *,
        current_page: int,
        page_size: int,
        word: str | None = None,
        domain_id: int | None = None,
        dataset_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        normalized_page_size = max(10, page_size)
        terms = self._filtered_terms(
            oid,
            word=word,
            domain_id=domain_id,
            dataset_ids=dataset_ids or [],
        )
        total_count = len(terms)
        total_pages = (
            (total_count + normalized_page_size - 1) // normalized_page_size
            if total_count
            else 0
        )
        normalized_page = (
            max(1, min(current_page, total_pages)) if total_pages else 1
        )
        start = (normalized_page - 1) * normalized_page_size
        data = [
            self._to_legacy_dto(term)
            for term in terms[start : start + normalized_page_size]
        ]
        return {
            "current_page": normalized_page,
            "page_size": normalized_page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "data": data,
        }

    def list_terms(
        self,
        oid: int,
        *,
        word: str | None = None,
        domain_id: int | None = None,
        dataset_ids: list[int] | None = None,
    ) -> list[LegacyTerminologyDTO]:
        return [
            self._to_legacy_dto(term)
            for term in self._filtered_terms(
                oid,
                word=word,
                domain_id=domain_id,
                dataset_ids=dataset_ids or [],
            )
        ]

    def create_term(self, oid: int, info: LegacyTerminologyDTO) -> int:
        term = self._term_service.create_term(
            oid,
            self._to_payload(info),
            enabled=info.enabled,
        )
        return self._term_id(term)

    def update_term(
        self,
        oid: int,
        term_id: int,
        info: LegacyTerminologyDTO,
    ) -> int:
        term = self._term_service.update_term(
            oid,
            term_id,
            self._to_payload(info),
            enabled=info.enabled,
        )
        return self._term_id(term)

    def delete_terms(self, oid: int, term_ids: list[int]) -> None:
        self._term_service.delete_terms(oid, term_ids)

    def set_term_enabled(
        self,
        oid: int,
        term_id: int,
        enabled: bool,
    ) -> None:
        self._term_service.set_term_enabled(oid, term_id, enabled)

    def _filtered_terms(
        self,
        oid: int,
        *,
        word: str | None,
        domain_id: int | None,
        dataset_ids: list[int],
    ) -> list[SemanticTerm]:
        normalized_dataset_ids = set(self._positive_ids(dataset_ids))
        normalized_word = str(word or "").strip().casefold()
        terms = self._term_service.list_terms(oid, domain_id)
        result = [
            term
            for term in terms
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

    def _to_payload(self, info: LegacyTerminologyDTO) -> TermPayload:
        if info.domain_id is None or info.domain_id <= 0:
            raise SemanticValidationError("SEMANTIC_TERM_DOMAIN_REQUIRED")
        if info.datasource_ids or info.datasource_names:
            raise SemanticValidationError(
                "SEMANTIC_TERM_DATASOURCE_SCOPE_UNSUPPORTED"
            )
        dataset_ids = self._positive_ids(info.dataset_ids)
        if info.specific_ds and not dataset_ids:
            raise SemanticValidationError("SEMANTIC_TERM_DATASET_SCOPE_REQUIRED")

        metric_ids: list[int] = []
        dimension_ids: list[int] = []
        for asset in info.mapped_assets:
            asset_type = str(
                asset.get("asset_type")
                or asset.get("assetType")
                or asset.get("type")
                or ""
            ).upper()
            asset_id = self._positive_id(
                asset.get("asset_id") or asset.get("assetId") or asset.get("id")
            )
            if asset_type not in {"METRIC", "DIMENSION"} or asset_id is None:
                raise SemanticValidationError("SEMANTIC_TERM_ASSET_INVALID")
            target = metric_ids if asset_type == "METRIC" else dimension_ids
            if asset_id not in target:
                target.append(asset_id)

        return TermPayload(
            domain_id=info.domain_id,
            name=str(info.word or ""),
            description=info.description,
            alias=[*info.other_words, *info.aliases],
            related_datasets=dataset_ids,
            related_metrics=metric_ids,
            related_dimensions=dimension_ids,
        )

    @staticmethod
    def _to_legacy_dto(term: SemanticTerm) -> LegacyTerminologyDTO:
        mapped_assets = [
            {"asset_type": "METRIC", "asset_id": asset_id}
            for asset_id in term.related_metrics
        ]
        mapped_assets.extend(
            {"asset_type": "DIMENSION", "asset_id": asset_id}
            for asset_id in term.related_dimensions
        )
        return LegacyTerminologyDTO(
            id=term.id,
            domain_id=term.domain_id,
            create_time=term.created_at,
            word=term.name,
            description=term.description,
            other_words=list(term.alias),
            aliases=list(term.alias),
            specific_ds=bool(term.related_datasets),
            dataset_ids=list(term.related_datasets),
            mapped_assets=mapped_assets,
            enabled=term.status == 1,
        )

    @staticmethod
    def _positive_ids(values: list[int]) -> list[int]:
        result: list[int] = []
        for value in values:
            item = LegacyTerminologyCompatibilityService._positive_id(value)
            if item is None:
                raise SemanticValidationError(
                    "SEMANTIC_TERM_REFERENCE_ID_INVALID"
                )
            if item not in result:
                result.append(item)
        return result

    @staticmethod
    def _positive_id(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _term_id(term: SemanticTerm) -> int:
        if term.id is None:
            raise SemanticDataAccessError("SEMANTIC_TERM_ID_MISSING")
        return term.id


__all__ = ["LegacyTerminologyCompatibilityService"]
