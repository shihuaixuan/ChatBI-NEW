from __future__ import annotations

from sqlalchemy import or_, select

from apps.agentic_chat.services.query_understanding_models import SlotCandidate
from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.enums import AssetType as RuntimeAssetType
from apps.semantic.assets.runtime_service import RuntimeAssetService
from apps.semantic.models.semantic_model import (
    AssetStatus,
    SemanticDimension,
    SemanticMetric,
)
from apps.terminology.models.terminology_model import Terminology
from common.core.config import settings


class QueryUnderstandingCandidateBuilder:
    def __init__(self, session=None, metric_limit: int = 8, dimension_limit: int = 10, terminology_limit: int = 8, field_limit: int = 40):
        self.session = session
        self.metric_limit = metric_limit
        self.dimension_limit = dimension_limit
        self.terminology_limit = terminology_limit
        self.field_limit = field_limit

    def build_semantic_candidates(self, datasource_id: int | None, oid: int = 1, user_id: int | None = None) -> list[SlotCandidate]:
        if not datasource_id or self.session is None or not hasattr(self.session, "exec"):
            return []
        if settings.SEMANTIC_ASSET_RUNTIME_ENABLED:
            runtime_candidates = self._build_runtime_asset_candidates(datasource_id=datasource_id, oid=oid)
            if runtime_candidates:
                return runtime_candidates
        if not settings.SEMANTIC_ASSET_LEGACY_FALLBACK_ENABLED:
            return []
        return self._build_legacy_semantic_candidates(datasource_id=datasource_id, oid=oid, user_id=user_id)

    def _build_runtime_asset_candidates(self, datasource_id: int, oid: int) -> list[SlotCandidate]:
        try:
            scope = DatasetScope.virtual(oid=oid, datasource_id=datasource_id)
            runtime_schema = RuntimeAssetService(session=self.session).load_runtime_schema(scope)
        except Exception:
            return []

        grouped_candidates = (
            runtime_schema.candidate_groups.metrics[: self.metric_limit]
            + runtime_schema.candidate_groups.dimensions[: self.dimension_limit]
            + runtime_schema.candidate_groups.dimension_values[: self.dimension_limit]
        )
        candidates: list[SlotCandidate] = []
        for candidate in grouped_candidates:
            asset_type = self._to_understanding_asset_type(candidate.asset_type)
            if asset_type is None:
                continue
            candidates.append(
                SlotCandidate(
                    display_name=candidate.title,
                    raw_text=candidate.title,
                    asset_type=asset_type,
                    asset_id=int(candidate.asset_id) if isinstance(candidate.asset_id, int) or str(candidate.asset_id).isdigit() else None,
                    score=candidate.score,
                    source="runtime_asset",
                    metadata={
                        **candidate.metadata,
                        "relations": candidate.relations,
                        "evidence": [item.model_dump() for item in candidate.evidence],
                        "dataset_id": candidate.dataset_id,
                    },
                )
            )
        return candidates

    def _to_understanding_asset_type(self, asset_type: RuntimeAssetType) -> str | None:
        if asset_type == RuntimeAssetType.METRIC:
            return "METRIC"
        if asset_type == RuntimeAssetType.DIMENSION:
            return "DIMENSION"
        if asset_type == RuntimeAssetType.DIMENSION_VALUE:
            return "VALUE"
        return None

    def _build_legacy_semantic_candidates(self, datasource_id: int, oid: int = 1, user_id: int | None = None) -> list[SlotCandidate]:
        metric_conditions = [
            SemanticMetric.oid == oid,
            SemanticMetric.datasource_id == datasource_id,
            SemanticMetric.status == AssetStatus.APPROVED.value,
        ]
        if user_id is not None:
            metric_conditions.append(or_(SemanticMetric.owner_id.is_(None), SemanticMetric.owner_id == user_id))
        metrics = self.session.exec(
            select(SemanticMetric).where(*metric_conditions)
            .order_by(SemanticMetric.updated_at.desc())
            .limit(self.metric_limit)
        ).scalars().all()
        dimension_conditions = [
            SemanticDimension.oid == oid,
            SemanticDimension.datasource_id == datasource_id,
            SemanticDimension.status == AssetStatus.APPROVED.value,
        ]
        if user_id is not None:
            dimension_conditions.append(or_(SemanticDimension.owner_id.is_(None), SemanticDimension.owner_id == user_id))
        dimensions = self.session.exec(
            select(SemanticDimension).where(*dimension_conditions)
            .order_by(SemanticDimension.updated_at.desc())
            .limit(self.dimension_limit)
        ).scalars().all()
        candidates: list[SlotCandidate] = []
        for metric in metrics:
            candidates.append(
                SlotCandidate(
                    display_name=metric.display_name,
                    raw_text=metric.display_name,
                    asset_type="METRIC",
                    asset_id=metric.id,
                    score=0.75,
                    source="semantic_metric",
                    metadata={
                        "name": metric.name,
                        "aliases": metric.aliases or [],
                        "description": metric.description,
                        "expr": metric.expr,
                    },
                )
            )
        for dimension in dimensions:
            candidates.append(
                SlotCandidate(
                    display_name=dimension.display_name,
                    raw_text=dimension.display_name,
                    asset_type="DIMENSION",
                    asset_id=dimension.id,
                    score=0.75,
                    source="semantic_dimension",
                    metadata={
                        "name": dimension.name,
                        "aliases": dimension.aliases or [],
                        "description": dimension.description,
                        "expr": dimension.expr,
                        "dimension_type": dimension.dimension_type,
                        "semantic_type": dimension.semantic_type,
                    },
                )
            )
        return sorted(candidates, key=lambda item: item.score, reverse=True)[: self.metric_limit + self.dimension_limit]

    def build_schema_summary(self, datasource_id: int | None) -> list[dict]:
        if not datasource_id or self.session is None or not hasattr(self.session, "exec"):
            return []
        tables = self.session.exec(
            select(CoreTable).where(CoreTable.ds_id == datasource_id, CoreTable.checked.is_(True))
        ).scalars().all()
        summary = []
        for table in tables:
            fields = self.session.exec(
                select(CoreField).where(CoreField.table_id == table.id, CoreField.checked.is_(True))
                .limit(self.field_limit)
            ).scalars().all()
            summary.append(
                {
                    "table": table.table_name,
                    "comment": table.custom_comment or table.table_comment,
                    "fields": [
                        {
                            "name": field.field_name,
                            "type": field.field_type,
                            "comment": field.custom_comment or field.field_comment,
                        }
                        for field in fields
                    ],
                }
            )
        return summary

    def build_terminology_candidates(self, datasource_id: int | None, question: str) -> list[dict]:
        if not datasource_id or self.session is None or not hasattr(self.session, "exec"):
            return []
        terms = self.session.exec(
            select(Terminology).where(Terminology.enabled.is_(True))
        ).scalars().all()
        candidates = []
        for term in terms:
            datasource_ids = term.datasource_ids or []
            if term.specific_ds and datasource_id not in datasource_ids:
                continue
            text = f"{term.word or ''} {term.description or ''}"
            if term.word and term.word in question:
                score = 0.9
            elif any(part and part in question for part in re_split_term(text)):
                score = 0.65
            else:
                continue
            candidates.append(
                {
                    "term": term.word,
                    "aliases": [],
                    "description": term.description,
                    "mapped_assets": [],
                    "score": score,
                }
            )
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[: self.terminology_limit]


def re_split_term(text: str) -> list[str]:
    return [part.strip() for part in text.replace("，", ",").replace("、", ",").split(",") if part.strip()]
