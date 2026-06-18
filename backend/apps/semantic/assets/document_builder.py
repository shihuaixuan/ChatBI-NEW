from __future__ import annotations

from typing import Any

from sqlalchemy import select

from apps.data_training.models.data_training_model import DataTraining
from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.assets.dataset_profile import DatasetScope
from apps.semantic.assets.enums import AssetType, RelationType
from apps.semantic.assets.models import AssetRetrievalDocumentRuntime
from apps.semantic.models.semantic_model import (
    AssetStatus,
    DimensionType,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
)
from apps.terminology.models.terminology_model import Terminology


class AssetDocumentBuilder:
    def __init__(self, session: Any | None = None):
        self.session = session

    def build_documents(self, scope: DatasetScope) -> list[AssetRetrievalDocumentRuntime]:
        documents: list[AssetRetrievalDocumentRuntime] = []
        if self.session is None or not hasattr(self.session, "exec") or scope.datasource_id is None:
            return documents

        metrics = self._load_metrics(scope)
        dimensions = self._load_dimensions(scope)
        dimension_by_id = {dimension.id: dimension for dimension in dimensions if dimension.id is not None}

        for metric in metrics:
            documents.append(self.build_metric_document(metric, scope))
        for dimension in dimensions:
            documents.append(self.build_dimension_document(dimension, scope))
        for value in self._load_dimension_values(list(dimension_by_id)):
            dimension = dimension_by_id.get(value.dimension_id)
            if dimension is None:
                continue
            document = self.build_dimension_value_document(value, dimension, scope)
            if document is not None:
                documents.append(document)
        for term in self._load_terms(scope):
            documents.append(self.build_term_document(term, scope))
        for example in self._load_examples(scope):
            documents.append(self.build_example_document(example, scope))
        for field, table in self._load_fields(scope):
            documents.append(self.build_field_document(field, table, scope))
        return documents

    def load_quality_assets(self, scope: DatasetScope) -> list[Any]:
        if self.session is None or not hasattr(self.session, "exec") or scope.datasource_id is None:
            return []
        metrics = self._load_metrics(scope)
        dimensions = self._load_dimensions(scope)
        dimension_ids = [dimension.id for dimension in dimensions if dimension.id is not None]
        return [
            *metrics,
            *dimensions,
            *self._load_dimension_values(dimension_ids),
            *self._load_terms(scope),
            *self._load_examples(scope),
        ]

    def build_metric_document(self, metric: SemanticMetric, scope: DatasetScope) -> AssetRetrievalDocumentRuntime:
        aliases = _unique(metric.aliases or [])
        relations = [
            {
                "relation_type": RelationType.ANALYZABLE_BY.value,
                "asset_type": AssetType.DIMENSION.value,
                "asset_id": dimension_id,
            }
            for dimension_id in (metric.related_dimension_ids or [])
        ]
        if metric.field_id is not None:
            relations.append(
                {
                    "relation_type": RelationType.USES_FIELD.value,
                    "asset_type": AssetType.FIELD.value,
                    "asset_id": metric.field_id,
                }
            )
        group = getattr(metric, "metric_group", None)
        related_terms = [str(term_id) for term_id in getattr(metric, "related_term_ids", None) or []]
        related_examples = [str(example_id) for example_id in getattr(metric, "related_example_ids", None) or []]
        search_text = build_search_text(
            metric.display_name,
            metric.name,
            aliases,
            metric.description,
            metric.expr,
            metric.default_agg,
            group,
            related_terms,
            related_examples,
        )
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.METRIC.value}:{metric.id}",
            asset_type=AssetType.METRIC,
            asset_id=metric.id or metric.name,
            dataset_id=scope.dataset_id,
            title=metric.display_name,
            aliases=aliases,
            business_text=metric.description,
            technical_text=metric.expr,
            related_terms=related_terms,
            related_examples=related_examples,
            relations=relations,
            search_text=search_text,
            metadata={
                "name": metric.name,
                "default_agg": metric.default_agg,
                "metric_group": group,
                "data_type": metric.data_type,
                "data_format": metric.data_format,
            },
        )

    def build_dimension_document(self, dimension: SemanticDimension, scope: DatasetScope) -> AssetRetrievalDocumentRuntime:
        aliases = _unique(dimension.aliases or [])
        relations = []
        if dimension.field_id is not None:
            relations.append(
                {
                    "relation_type": RelationType.USES_FIELD.value,
                    "asset_type": AssetType.FIELD.value,
                    "asset_id": dimension.field_id,
                }
            )
        search_text = build_search_text(
            dimension.display_name,
            dimension.name,
            aliases,
            dimension.description,
            dimension.expr,
            dimension.dimension_type,
            dimension.semantic_type,
            dimension.default_values,
        )
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.DIMENSION.value}:{dimension.id}",
            asset_type=AssetType.DIMENSION,
            asset_id=dimension.id or dimension.name,
            dataset_id=scope.dataset_id,
            title=dimension.display_name,
            aliases=aliases,
            business_text=dimension.description,
            technical_text=dimension.expr,
            relations=relations,
            search_text=search_text,
            metadata={
                "name": dimension.name,
                "dimension_type": dimension.dimension_type,
                "semantic_type": dimension.semantic_type,
                "time_granularities": dimension.time_granularities or [],
            },
        )

    def build_dimension_value_document(
        self,
        value: SemanticDimensionValue,
        dimension: SemanticDimension,
        scope: DatasetScope,
    ) -> AssetRetrievalDocumentRuntime | None:
        if not value.enabled or self._should_skip_dimension_value(value, dimension):
            return None
        title = value.display_value or value.value
        aliases = _unique(value.aliases or [])
        search_text = build_search_text(title, value.value, aliases, dimension.display_name, dimension.name)
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.DIMENSION_VALUE.value}:{value.id}",
            asset_type=AssetType.DIMENSION_VALUE,
            asset_id=value.id or f"{dimension.id}:{value.value}",
            dataset_id=scope.dataset_id,
            title=title,
            aliases=aliases,
            business_text=dimension.display_name,
            technical_text=value.value,
            relations=[
                {
                    "relation_type": RelationType.BELONGS_TO.value,
                    "asset_type": AssetType.DIMENSION.value,
                    "asset_id": dimension.id,
                }
            ],
            search_text=search_text,
            metadata={
                "dimension_id": dimension.id,
                "dimension_name": dimension.name,
                "value": value.value,
            },
        )

    def build_term_document(self, term: Terminology, scope: DatasetScope) -> AssetRetrievalDocumentRuntime:
        aliases = _unique(getattr(term, "aliases", None) or getattr(term, "other_words", None) or [])
        mapped_assets = getattr(term, "mapped_assets", None) or []
        search_text = build_search_text(term.word, aliases, term.description)
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.TERM.value}:{term.id}",
            asset_type=AssetType.TERM,
            asset_id=term.id or term.word or "",
            dataset_id=scope.dataset_id,
            title=term.word or "",
            aliases=aliases,
            business_text=term.description,
            relations=mapped_assets,
            search_text=search_text,
            metadata={"datasource_ids": term.datasource_ids or []},
        )

    def build_example_document(self, example: DataTraining, scope: DatasetScope) -> AssetRetrievalDocumentRuntime:
        example_type = getattr(example, "example_type", None) or "QUESTION_EXAMPLE"
        linked_assets = getattr(example, "linked_assets", None) or []
        sql = getattr(example, "sql", None)
        search_text = build_search_text(example.question, example.description, example_type, _truncate(sql or ""))
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.EXAMPLE.value}:{example.id}",
            asset_type=AssetType.EXAMPLE,
            asset_id=example.id or example.question or "",
            dataset_id=scope.dataset_id,
            title=example.question or "",
            business_text=example.description,
            technical_text=_truncate(sql or ""),
            relations=linked_assets,
            search_text=search_text,
            metadata={"example_type": example_type},
        )

    def build_field_document(self, field: CoreField, table: CoreTable | None, scope: DatasetScope) -> AssetRetrievalDocumentRuntime:
        title = field.custom_comment or field.field_comment or field.field_name
        search_text = build_search_text(
            title,
            field.field_name,
            field.field_type,
            table.table_name if table else None,
            table.custom_comment if table else None,
            table.table_comment if table else None,
        )
        return AssetRetrievalDocumentRuntime(
            doc_id=f"{AssetType.FIELD.value}:{field.id}",
            asset_type=AssetType.FIELD,
            asset_id=field.id,
            dataset_id=scope.dataset_id,
            title=title,
            business_text=field.custom_comment or field.field_comment,
            technical_text=field.field_name,
            search_text=search_text,
            metadata={
                "field_name": field.field_name,
                "field_type": field.field_type,
                "table_id": field.table_id,
                "table_name": table.table_name if table else None,
                "semantic_role": self._infer_field_role(field),
            },
        )

    def _load_metrics(self, scope: DatasetScope) -> list[SemanticMetric]:
        conditions = [
            SemanticMetric.oid == scope.oid,
            SemanticMetric.datasource_id == scope.datasource_id,
            SemanticMetric.status == AssetStatus.APPROVED.value,
        ]
        if scope.table_ids:
            conditions.append(SemanticMetric.table_id.in_(scope.table_ids))
        return _all(self.session.exec(select(SemanticMetric).where(*conditions)))

    def _load_dimensions(self, scope: DatasetScope) -> list[SemanticDimension]:
        conditions = [
            SemanticDimension.oid == scope.oid,
            SemanticDimension.datasource_id == scope.datasource_id,
            SemanticDimension.status == AssetStatus.APPROVED.value,
        ]
        if scope.table_ids:
            conditions.append(SemanticDimension.table_id.in_(scope.table_ids))
        return _all(self.session.exec(select(SemanticDimension).where(*conditions)))

    def _load_dimension_values(self, dimension_ids: list[int]) -> list[SemanticDimensionValue]:
        if not dimension_ids:
            return []
        return _all(
            self.session.exec(
                select(SemanticDimensionValue).where(
                    SemanticDimensionValue.dimension_id.in_(dimension_ids),
                    SemanticDimensionValue.enabled.is_(True),
                )
            )
        )

    def _load_terms(self, scope: DatasetScope) -> list[Terminology]:
        terms = _all(self.session.exec(select(Terminology).where(Terminology.enabled.is_(True))))
        return [
            term
            for term in terms
            if not term.specific_ds or scope.datasource_id in (term.datasource_ids or [])
        ]

    def _load_examples(self, scope: DatasetScope) -> list[DataTraining]:
        conditions = [DataTraining.enabled.is_(True)]
        if scope.datasource_id is not None:
            conditions.append(DataTraining.datasource == scope.datasource_id)
        return _all(self.session.exec(select(DataTraining).where(*conditions)))

    def _load_fields(self, scope: DatasetScope) -> list[tuple[CoreField, CoreTable | None]]:
        table_conditions = [CoreTable.ds_id == scope.datasource_id, CoreTable.checked.is_(True)]
        if scope.table_ids:
            table_conditions.append(CoreTable.id.in_(scope.table_ids))
        tables = _all(self.session.exec(select(CoreTable).where(*table_conditions)))
        table_by_id = {table.id: table for table in tables}
        if not table_by_id:
            return []
        fields = _all(
            self.session.exec(
                select(CoreField).where(CoreField.table_id.in_(list(table_by_id)), CoreField.checked.is_(True))
            )
        )
        return [(field, table_by_id.get(field.table_id)) for field in fields]

    def _should_skip_dimension_value(self, value: SemanticDimensionValue, dimension: SemanticDimension) -> bool:
        if dimension.dimension_type == DimensionType.ID.value and not value.aliases and not value.display_value:
            return True
        text = f"{dimension.name} {dimension.display_name} {dimension.description or ''}".lower()
        if any(word in text for word in ("phone", "mobile", "手机号", "用户id", "客户id", "订单id", "sku")):
            return not value.aliases and not value.display_value
        return False

    def _infer_field_role(self, field: CoreField) -> str:
        text = f"{field.field_name} {field.field_comment or ''} {field.custom_comment or ''}".lower()
        if any(word in text for word in ("date", "time", "日期", "时间")):
            return "time-like"
        if any(word in text for word in ("amount", "count", "num", "uv", "pv", "金额", "数量", "人数", "次数")):
            return "metric-like"
        return "dimension-like"


def build_search_text(*parts: Any) -> str:
    tokens: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            tokens.extend(str(item).strip() for item in part if item is not None and str(item).strip())
        else:
            text = str(part).strip()
            if text:
                tokens.append(text)
    return " ".join(_unique(tokens))


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _truncate(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit]


def _all(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "all"):
        return result.all()
    return list(result or [])
