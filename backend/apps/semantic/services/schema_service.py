from __future__ import annotations

from typing import Protocol

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import (
    DatasetSchema,
    Ontology,
    SchemaMapInfo,
    SchemaMapRequest,
)
from apps.semantic.repository.schema_repository import SchemaRepository
from apps.semantic.services.builders.schema_builder import (
    SemanticSchemaBuilder,
    build_ontology_from_schema,
)
from apps.semantic.services.matching.schema_element_matcher import SchemaElementMatcher


class DatasetSchemaProvider(Protocol):
    """向调用方提供可用数据集 Schema 的应用边界。"""

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema: ...


class SemanticSchemaService:
    """数据集 Schema 查询与映射应用服务。"""

    def __init__(self, repository: SchemaRepository):
        self._repository = repository
        self._builder = SemanticSchemaBuilder()

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema:
        return self._builder.build(self._repository.load(oid, dataset_id))

    def get_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema:
        try:
            return self.build_dataset_schema(oid, dataset_id)
        except ValueError as error:
            raise SemanticNotFoundError(str(error)) from error

    def get_dataset_ontology(self, oid: int, dataset_id: int) -> Ontology:
        return build_ontology_from_schema(self.get_dataset_schema(oid, dataset_id))

    def map_schema(self, oid: int, payload: SchemaMapRequest) -> SchemaMapInfo:
        matcher = SchemaElementMatcher()
        merged = SchemaMapInfo()
        for dataset_id in payload.dataset_ids:
            schema = self.get_dataset_schema(oid, dataset_id)
            map_info = matcher.match(payload.query_text, schema)
            merged.data_set_element_matches.update(
                map_info.data_set_element_matches
            )
        return merged
