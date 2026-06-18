from __future__ import annotations

from typing import Any

from apps.semantic.assets.cache import RuntimeAssetCache
from apps.semantic.assets.dataset_profile import DatasetProfileService, DatasetScope
from apps.semantic.assets.document_builder import AssetDocumentBuilder
from apps.semantic.assets.enums import AssetType
from apps.semantic.assets.models import (
    AssetEvidence,
    CandidateAsset,
    CandidateGroup,
    RuntimeSchema,
)


class RuntimeAssetService:
    def __init__(self, session: Any | None = None, cache: RuntimeAssetCache | None = None):
        self.session = session
        self.cache = cache
        self.profile_service = DatasetProfileService(session=session)
        self.document_builder = AssetDocumentBuilder(session=session)

    def load_runtime_schema(self, scope: DatasetScope) -> RuntimeSchema:
        if self.cache is not None:
            cached = self.cache.get(scope)
            if cached is not None:
                return cached
        profile = self.profile_service.build_profile(scope)
        documents = self.document_builder.build_documents(scope)
        runtime_schema = RuntimeSchema(
            dataset_profile=profile,
            documents=documents,
            candidate_groups=self._build_candidate_groups(documents),
        )
        if self.cache is not None:
            self.cache.set(scope, runtime_schema)
        return runtime_schema

    def _build_candidate_groups(self, documents) -> CandidateGroup:
        groups = CandidateGroup()
        for document in documents:
            candidate = CandidateAsset(
                asset_type=document.asset_type,
                asset_id=document.asset_id,
                dataset_id=document.dataset_id,
                title=document.title,
                score=self._default_score(document.asset_type),
                evidence=[
                    AssetEvidence(
                        channel="runtime_document",
                        matched_field="search_text",
                        matched_text=document.search_text,
                        score=self._default_score(document.asset_type),
                        reason="由运行时资产检索文档生成候选",
                    )
                ],
                relations=document.relations,
                metadata={
                    **document.metadata,
                    "doc_id": document.doc_id,
                    "aliases": document.aliases,
                    "business_text": document.business_text,
                    "technical_text": document.technical_text,
                    "search_text": document.search_text,
                },
            )
            if document.asset_type == AssetType.METRIC:
                groups.metrics.append(candidate)
            elif document.asset_type == AssetType.DIMENSION:
                groups.dimensions.append(candidate)
            elif document.asset_type == AssetType.DIMENSION_VALUE:
                groups.dimension_values.append(candidate)
            elif document.asset_type == AssetType.TERM:
                groups.terms.append(candidate)
            elif document.asset_type == AssetType.EXAMPLE:
                groups.examples.append(candidate)
            elif document.asset_type == AssetType.FIELD:
                groups.fields.append(candidate)
        return groups

    def _default_score(self, asset_type: AssetType) -> float:
        if asset_type in {AssetType.METRIC, AssetType.DIMENSION}:
            return 0.75
        if asset_type in {AssetType.TERM, AssetType.EXAMPLE, AssetType.DIMENSION_VALUE}:
            return 0.68
        return 0.45
