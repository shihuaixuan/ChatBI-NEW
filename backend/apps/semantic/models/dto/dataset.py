from typing import Any

from pydantic import Field, field_validator

from apps.semantic.models.dto.base import SemanticBaseDTO


class DatasetPayload(SemanticBaseDTO):
    domain_id: int
    name: str
    biz_name: str
    description: str | None = None
    alias: list[str] = Field(default_factory=list)
    data_set_detail: dict[str, Any] = Field(
        default_factory=lambda: {"dataSetModelConfigs": []}
    )
    query_config: dict[str, Any] = Field(default_factory=dict)
    owner: str | None = None

    @field_validator("query_config")
    @classmethod
    def validate_query_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        """限制语义执行策略为文档约定的三态。"""

        raw = value.get("semanticEnforcement", "LEGACY")
        normalized = str(raw).upper()
        if normalized not in {"STRICT", "ASSISTED", "LEGACY"}:
            raise ValueError("SEMANTIC_ENFORCEMENT_INVALID")
        return {**value, "semanticEnforcement": normalized}
