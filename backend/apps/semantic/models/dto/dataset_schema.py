from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator

from apps.semantic.models.dto.base import SemanticBaseDTO
from apps.semantic.models.dto.semantic_contract import (
    DimensionHierarchyRuntimeDTO,
    MetricRelationshipRuntimeDTO,
)


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


class DatasetCalendarContract(SemanticBaseDTO):
    """运行时冻结的数据集业务日历契约。"""

    # 仅用于未携带日历字段的旧内存 Schema；持久化数据集始终覆盖此默认值。
    default_timezone: str = "Asia/Shanghai"
    calendar_type: Literal["NATURAL", "FISCAL", "BUSINESS"] = "NATURAL"
    week_start_day: int = Field(default=1, ge=1, le=7)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    holiday_calendar_key: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """确保运行时 Schema 不携带无法解释的时区。"""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("SEMANTIC_DATASET_TIMEZONE_INVALID") from error
        return value


class DatasetSchema(SemanticBaseDTO):
    database_type: str | None = None
    database_version: str | None = None
    data_set: SchemaElement
    calendar: DatasetCalendarContract = Field(default_factory=DatasetCalendarContract)
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
    dimension_hierarchies: list[DimensionHierarchyRuntimeDTO] = Field(default_factory=list)
    research_relationships: list[MetricRelationshipRuntimeDTO] = Field(default_factory=list)
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
