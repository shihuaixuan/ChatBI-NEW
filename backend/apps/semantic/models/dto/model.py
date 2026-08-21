from typing import Any, Literal

from pydantic import ConfigDict, Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class ModelPayload(SemanticBaseDTO):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

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
    primary_key: list[str] = Field(default_factory=list)
    model_grain: list[str] = Field(default_factory=list)
    default_time_field: str | None = None
    model_kind: Literal[
        "ENTITY", "FACT", "DETAIL", "SNAPSHOT", "BRIDGE"
    ] | None = None
    row_description: str | None = None
    event_time_field: str | None = None
    snapshot_time_field: str | None = None


class ModelRelationPayload(SemanticBaseDTO):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    domain_id: int
    left_model_id: int
    right_model_id: int
    join_type: str = "left join"
    join_conditions: list[dict[str, Any]] = Field(default_factory=list)
    ext: dict[str, Any] = Field(default_factory=dict)
    cardinality: Literal[
        "ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"
    ] | None = None
    left_unique: bool | None = None
    right_unique: bool | None = None
    metric_propagation: Literal["LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "BOTH", "NONE"] | None = None
    aggregation_safety: Literal[
        "SAFE", "PRE_AGGREGATE_REQUIRED", "FORBIDDEN"
    ] | None = None
    valid_time_condition: dict[str, Any] | None = None


class SemanticTableMeta(SemanticBaseDTO):
    id: int | None = None
    table_name: str
    table_comment: str | None = None
    checked: bool = True


class SemanticColumnMeta(SemanticBaseDTO):
    id: int | None = None
    field_name: str
    field_type: str | None = None
    field_comment: str | None = None
    field_index: int = 0
    checked: bool = True


class ModelBuildSchemaPayload(SemanticBaseDTO):
    datasource_id: int
    source_type: str = "TABLE"
    table_name: str | None = None
    sql: str | None = None
    columns: list[SemanticColumnMeta] = Field(default_factory=list)


class ModelBuildField(SemanticBaseDTO):
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


class ModelBuildSchemaResult(SemanticBaseDTO):
    source_type: str = "TABLE"
    table_name: str | None = None
    sql: str | None = None
    fields: list[ModelBuildField] = Field(default_factory=list)
    model_detail: dict[str, Any] = Field(default_factory=dict)


class ModelCreateWithAssetsPayload(ModelPayload):
    table_name: str | None = None
    sql: str | None = None
