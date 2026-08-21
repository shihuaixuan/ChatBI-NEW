from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from apps.semantic.models.dto.base import SemanticBaseDTO


class DatasetPayload(SemanticBaseDTO):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    model_configs: list["DatasetModelConfigPayload"] = Field(
        default_factory=list, alias="modelConfigs"
    )
    assets: list["DatasetAssetPayload"] = Field(default_factory=list)
    query_config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None
    default_timezone: str = "UTC"
    calendar_type: Literal["NATURAL", "FISCAL", "BUSINESS"] = "NATURAL"
    week_start_day: int = Field(default=1, ge=1, le=7)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    holiday_calendar_key: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def validate_default_timezone(cls, value: str) -> str:
        """只接受时区数据库中存在的时区名称。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("SEMANTIC_DATASET_TIMEZONE_REQUIRED")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise ValueError("SEMANTIC_DATASET_TIMEZONE_INVALID") from error
        return normalized

    @model_validator(mode="after")
    def validate_calendar(self) -> "DatasetPayload":
        """校验财务日历和营业日历的最小配置。"""

        if self.calendar_type == "BUSINESS" and not self.holiday_calendar_key:
            raise ValueError("SEMANTIC_BUSINESS_CALENDAR_KEY_REQUIRED")
        return self

    @field_validator("query_config")
    @classmethod
    def validate_query_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        """拒绝已经移除的旧语义运行时配置。"""

        if "semanticEnforcement" in value:
            raise ValueError("SEMANTIC_ENFORCEMENT_REMOVED")
        if {"dimension_hierarchies", "research_relationships"} & value.keys():
            raise ValueError("SEMANTIC_RESEARCH_CONFIG_REMOVED")
        return value


class DatasetModelConfigPayload(SemanticBaseDTO):
    """数据集正式模型配置入参。"""

    model_id: int = Field(gt=0, alias="modelId")
    includes_all: bool = Field(default=False, alias="includesAll")
    is_default: bool = Field(default=False, alias="isDefault")
    sort_order: int = Field(default=0, alias="sortOrder")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class DatasetAssetPayload(SemanticBaseDTO):
    """数据集正式语义资产引用入参。"""

    asset_type: Literal[
        "METRIC",
        "DIMENSION",
        "DIMENSION_HIERARCHY",
        "METRIC_RELATIONSHIP",
    ] = Field(alias="assetType")
    asset_id: int = Field(gt=0, alias="assetId")
    model_id: int | None = Field(default=None, alias="modelId")
    is_default: bool = Field(default=False, alias="isDefault")
    sort_order: int = Field(default=0, alias="sortOrder")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class DatasetModelConfigResponse(SemanticBaseDTO):
    """数据集正式模型配置响应，不携带入参的 camelCase 别名。"""

    model_id: int
    includes_all: bool = False
    is_default: bool = False
    sort_order: int = 0

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DatasetAssetResponse(SemanticBaseDTO):
    """数据集正式资产响应，不携带入参的 camelCase 别名。"""

    asset_type: Literal[
        "METRIC",
        "DIMENSION",
        "DIMENSION_HIERARCHY",
        "METRIC_RELATIONSHIP",
    ]
    asset_id: int
    model_id: int | None = None
    is_default: bool = False
    sort_order: int = 0

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DatasetResponse(SemanticBaseDTO):
    """数据集管理接口返回的正式资产视图。"""

    id: int
    oid: int
    status: int
    schema_version: int = 1
    contract_version: int = 0
    index_version: int = 0
    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    model_configs: list[DatasetModelConfigResponse] = Field(default_factory=list)
    assets: list[DatasetAssetResponse] = Field(default_factory=list)
    query_config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None
    default_timezone: str = "UTC"
    calendar_type: Literal["NATURAL", "FISCAL", "BUSINESS"] = "NATURAL"
    week_start_day: int = 1
    fiscal_year_start_month: int = 1
    holiday_calendar_key: str | None = None

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        serialize_by_alias=False,
    )
