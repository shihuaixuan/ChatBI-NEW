from typing import Any

from pydantic import Field

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
