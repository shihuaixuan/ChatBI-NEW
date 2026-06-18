from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HeadlessBaseDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DataSetModelConfig(BaseModel):
    id: int
    includes_all: bool = Field(default=False, alias="includesAll")
    metrics: list[int] = Field(default_factory=list)
    dimensions: list[int] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SchemaElement(HeadlessBaseDTO):
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


class JoinRelation(HeadlessBaseDTO):
    id: int | None = None
    left: str
    right: str
    join_type: str = Field(default="left join")
    join_condition: list[list[str]] = Field(default_factory=list)


class DataSetSchema(HeadlessBaseDTO):
    database_type: str | None = None
    database_version: str | None = None
    data_set: SchemaElement
    models: list[dict[str, Any]] = Field(default_factory=list)
    model_relations: list[JoinRelation] = Field(default_factory=list)
    metrics: list[SchemaElement] = Field(default_factory=list)
    dimensions: list[SchemaElement] = Field(default_factory=list)
    tags: list[SchemaElement] = Field(default_factory=list)
    dimension_values: list[SchemaElement] = Field(default_factory=list)
    terms: list[SchemaElement] = Field(default_factory=list)
    query_config: dict[str, Any] = Field(default_factory=dict)


class Ontology(HeadlessBaseDTO):
    database_type: str | None = None
    database_version: str | None = None
    model_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metric_map: dict[str, list[SchemaElement]] = Field(default_factory=dict)
    dimension_map: dict[str, list[SchemaElement]] = Field(default_factory=dict)
    join_relations: list[JoinRelation] = Field(default_factory=list)


class SchemaElementMatch(HeadlessBaseDTO):
    element: SchemaElement
    offset: int = 0
    similarity: float = 0
    detect_word: str
    word: str
    frequency: int = 0
    is_inherited: bool = False
    llm_matched: bool = False


class SchemaMapInfo(HeadlessBaseDTO):
    data_set_element_matches: dict[int, list[SchemaElementMatch]] = Field(default_factory=dict)


class DomainPayload(HeadlessBaseDTO):
    name: str
    biz_name: str
    description: str | None = None
    parent_id: int | None = None
    admin: str | None = None
    owner: str | None = None


class ModelPayload(HeadlessBaseDTO):
    domain_id: int
    datasource_id: int
    name: str
    biz_name: str
    description: str | None = None
    model_detail: dict[str, Any] = Field(default_factory=dict)
    filter_sql: str | None = None
    alias: list[str] = Field(default_factory=list)
    source_type: str = "TABLE"
    depends: list[dict[str, Any]] = Field(default_factory=list)
    table_name: str | None = None
    sql_query: str | None = None


class ModelRelationPayload(HeadlessBaseDTO):
    domain_id: int
    left_model_id: int
    right_model_id: int
    join_type: str = "left join"
    join_conditions: list[dict[str, Any]] = Field(default_factory=list)
    ext: dict[str, Any] = Field(default_factory=dict)


class HeadlessTableMeta(HeadlessBaseDTO):
    id: int | None = None
    table_name: str
    table_comment: str | None = None
    checked: bool = True


class HeadlessColumnMeta(HeadlessBaseDTO):
    id: int | None = None
    field_name: str
    field_type: str | None = None
    field_comment: str | None = None
    field_index: int = 0
    checked: bool = True


class ModelBuildSchemaPayload(HeadlessBaseDTO):
    datasource_id: int
    source_type: str = "TABLE"
    table_name: str | None = None
    sql: str | None = None
    columns: list[HeadlessColumnMeta] = Field(default_factory=list)


class ModelBuildField(HeadlessBaseDTO):
    field_name: str
    data_type: str | None = None
    name: str
    biz_name: str
    expr: str
    role: str
    alias: list[str] = Field(default_factory=list)
    default_agg: str | None = None
    semantic_type: str | None = None
    create_asset: bool = True


class ModelBuildSchemaResult(HeadlessBaseDTO):
    source_type: str = "TABLE"
    table_name: str | None = None
    sql: str | None = None
    fields: list[ModelBuildField] = Field(default_factory=list)
    model_detail: dict[str, Any] = Field(default_factory=dict)


class ModelCreateWithAssetsPayload(ModelPayload):
    table_name: str | None = None
    sql: str | None = None


class MetricPayload(HeadlessBaseDTO):
    model_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    default_agg: str | None = None
    type: str = "ATOMIC"
    define_type: str = "MEASURE"
    type_params: dict[str, Any] = Field(default_factory=dict)
    relate_dimensions: list[dict[str, Any]] = Field(default_factory=list)


class MetricBatchCreateFromMeasuresPayload(HeadlessBaseDTO):
    model_id: int
    measure_ids: list[int] = Field(default_factory=list)
    measure_biz_names: list[str] = Field(default_factory=list)


class DimensionPayload(HeadlessBaseDTO):
    model_id: int
    name: str
    biz_name: str
    description: str | None = None
    type: str = "categorical"
    semantic_type: str | None = None
    alias: list[str] = Field(default_factory=list)
    default_values: list[str] = Field(default_factory=list)
    dim_value_maps: list[dict[str, Any]] = Field(default_factory=list)
    type_params: dict[str, Any] = Field(default_factory=dict)
    expr: str | None = None
    data_type: str | None = None


class DataSetPayload(HeadlessBaseDTO):
    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    data_set_detail: dict[str, Any] = Field(default_factory=lambda: {"dataSetModelConfigs": []})
    query_config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None


class TermPayload(HeadlessBaseDTO):
    domain_id: int
    name: str
    alias: list[str] = Field(default_factory=list)
    description: str | None = None
    related_metrics: list[int] = Field(default_factory=list)
    related_dimensions: list[int] = Field(default_factory=list)


class SchemaMapRequest(HeadlessBaseDTO):
    query_text: str
    dataset_ids: list[int]
