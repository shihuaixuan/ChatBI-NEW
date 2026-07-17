from __future__ import annotations

from typing import Any

from apps.semantic.models import SemanticAssetDocument
from apps.semantic.schemas import DatasetSchema, SchemaElement
from apps.semantic.storage_sync import unique_texts


class SemanticAssetDocumentBuilder:
    def build_from_schema(
        self,
        schema: DatasetSchema,
        oid: int,
        index_version: int = 0,
    ) -> list[SemanticAssetDocument]:
        documents: list[SemanticAssetDocument] = []
        for element in [*schema.metrics, *schema.dimensions, *schema.dimension_values, *schema.terms]:
            documents.append(self._document_from_element(schema, element, oid, index_version))
        return documents

    def _document_from_element(
        self,
        schema: DatasetSchema,
        element: SchemaElement,
        oid: int,
        index_version: int,
    ) -> SemanticAssetDocument:
        business_text = self._business_text(element)
        technical_text = self._technical_text(element)
        alias_text = " ".join(unique_texts(element.alias or []))
        search_text = " ".join(unique_texts([element.name, element.biz_name, business_text, technical_text, alias_text]))
        return SemanticAssetDocument(
            oid=oid,
            dataset_id=schema.data_set.id,
            asset_type=element.type,
            asset_id=element.id,
            doc_key=f"{element.type}:{element.id}",
            title=element.name,
            business_text=business_text,
            technical_text=technical_text,
            alias_text=alias_text,
            search_text=search_text,
            payload=element.model_dump(),
            index_version=index_version,
            embedding_status="PENDING",
        )

    def _business_text(self, element: SchemaElement) -> str:
        parts: list[Any] = [element.description]
        if element.type == "DIMENSION":
            parts.extend([element.ext_info.get("dimension_type"), element.ext_info.get("semantic_type")])
        if element.type == "VALUE":
            for item in element.schema_value_maps or []:
                parts.extend([item.get("bizName"), item.get("biz_name"), *(item.get("alias") or [])])
        if element.type == "TERM":
            parts.extend(element.alias or [])
        return " ".join(unique_texts(parts))

    def _technical_text(self, element: SchemaElement) -> str:
        parts: list[Any] = [element.biz_name]
        if element.type == "METRIC":
            parts.extend(element.fields or [])
            parts.extend([element.type_params.get("expr"), element.default_agg])
        if element.type == "DIMENSION":
            parts.extend([element.ext_info.get("field_name"), element.ext_info.get("dimension_data_type")])
        if element.type == "VALUE":
            for item in element.schema_value_maps or []:
                parts.extend([item.get("value"), item.get("techName"), item.get("tech_name")])
        return " ".join(unique_texts(parts))
