from typing import Any

from pydantic import ConfigDict, Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class DatasetModelConfig(SemanticBaseDTO):
    id: int
    includes_all: bool = Field(default=False, alias="includesAll")
    metrics: list[int] = Field(default_factory=list)
    dimensions: list[int] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SchemaElement(SemanticBaseDTO):
    data_set_id: int
    data_set_name: str
    model: int | None = None
    id: int
    name: str
    biz_name: str
    use_cnt: int = 0
    type: str
    alias: list[str] = Field(default_factory=list)
    schema_value_maps: list[dict[str, Any]] = Field(default_factory=list)
    related_schema_elements: list[dict[str, Any]] = Field(default_factory=list)
    default_agg: str | None = None
    data_format_type: str | None = None
    order: float = 0
    is_tag: int = 0
    description: str | None = None
    ext_info: dict[str, Any] = Field(default_factory=dict)
    type_params: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)


class JoinRelation(SemanticBaseDTO):
    id: int | None = None
    left: str
    right: str
    join_type: str = Field(default="left join")
    join_condition: list[list[str]] = Field(default_factory=list)


class DatasetSchema(SemanticBaseDTO):
    database_type: str | None = None
    database_version: str | None = None
    data_set: SchemaElement
    subject_domains: list[dict[str, Any]] = Field(default_factory=list)
    models: list[dict[str, Any]] = Field(default_factory=list)
    model_relations: list[JoinRelation] = Field(default_factory=list)
    metrics: list[SchemaElement] = Field(default_factory=list)
    dimensions: list[SchemaElement] = Field(default_factory=list)
    tags: list[SchemaElement] = Field(default_factory=list)
    dimension_values: list[SchemaElement] = Field(default_factory=list)
    terms: list[SchemaElement] = Field(default_factory=list)
    query_config: dict[str, Any] = Field(default_factory=dict)
    instructions: dict[str, list[str]] = Field(default_factory=dict)
    business_entities: list[dict[str, Any]] = Field(default_factory=list)
    logical_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    metric_dimension_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    # Research 只消费已治理的层级和驱动关系，不能运行时自行推断。
    dimension_hierarchies: list[dict[str, Any]] = Field(default_factory=list)
    research_relationships: list[dict[str, Any]] = Field(default_factory=list)
    model_contracts: list[dict[str, Any]] = Field(default_factory=list)
    relation_contracts: list[dict[str, Any]] = Field(default_factory=list)
    metric_contracts: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = 1
    contract_version: int = 0
    asset_versions: dict[str, int] = Field(default_factory=dict)
    schema_fingerprint: str = ""


class Ontology(SemanticBaseDTO):
    database_type: str | None = None
    database_version: str | None = None
    model_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metric_map: dict[str, list[SchemaElement]] = Field(default_factory=dict)
    dimension_map: dict[str, list[SchemaElement]] = Field(default_factory=dict)
    join_relations: list[JoinRelation] = Field(default_factory=list)


class SchemaElementMatch(SemanticBaseDTO):
    element: SchemaElement
    offset: int = 0
    similarity: float = 0
    detect_word: str
    word: str
    frequency: int = 0
    is_inherited: bool = False
    llm_matched: bool = False


class SchemaMapInfo(SemanticBaseDTO):
    data_set_element_matches: dict[int, list[SchemaElementMatch]] = Field(
        default_factory=dict
    )


class SchemaMapRequest(SemanticBaseDTO):
    query_text: str
    dataset_ids: list[int]
