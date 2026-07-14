"""版本化检索 profile 定义。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.schemas import (
    RetrievalChannel,
    RetrievalProfileName,
    RetrievalResourceType,
    RetrievalSourceType,
)


class RetrievalProfileDefinition(BaseModel):
    """一个检索目标的通道、容量和安全属性。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: RetrievalProfileName
    version: str = Field(min_length=1)
    allowed_sources: tuple[RetrievalSourceType, ...]
    allowed_resource_types: tuple[RetrievalResourceType, ...]
    channels: tuple[RetrievalChannel, ...]
    recall_limits: dict[RetrievalChannel, int]
    fusion: Literal["none", "rrf"]
    rrf_k: int | None = Field(default=None, gt=0)
    rerank_limit: int = Field(default=0, ge=0)
    result_limit: int = Field(gt=0)
    requires_slot_decision: bool
    allows_executable_assets: bool

    @model_validator(mode="after")
    def validate_channel_configuration(self) -> RetrievalProfileDefinition:
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("profile 检索通道不允许重复")
        if set(self.recall_limits) != set(self.channels):
            raise ValueError("recall_limits 必须覆盖且只能覆盖已启用通道")
        if any(limit <= 0 for limit in self.recall_limits.values()):
            raise ValueError("每个检索通道的 recall limit 必须大于 0")
        if self.fusion == "rrf" and (len(self.channels) < 2 or self.rrf_k is None):
            raise ValueError("RRF 至少需要两个通道并配置 rrf_k")
        if self.fusion == "none" and self.rrf_k is not None:
            raise ValueError("未启用 RRF 时不能配置 rrf_k")
        return self


PROFILE_REGISTRY: dict[RetrievalProfileName, RetrievalProfileDefinition] = {
    RetrievalProfileName.SEMANTIC_BINDING: RetrievalProfileDefinition(
        name=RetrievalProfileName.SEMANTIC_BINDING,
        version="semantic-binding-v1",
        allowed_sources=(RetrievalSourceType.HEADLESS,),
        allowed_resource_types=(
            RetrievalResourceType.METRIC,
            RetrievalResourceType.DIMENSION,
            RetrievalResourceType.VALUE,
            RetrievalResourceType.TERM,
            RetrievalResourceType.MODEL,
            RetrievalResourceType.DATASET,
        ),
        channels=(
            RetrievalChannel.EXACT,
            RetrievalChannel.ALIAS,
            RetrievalChannel.LEXICAL,
            RetrievalChannel.DENSE,
            RetrievalChannel.RELATION,
        ),
        recall_limits={
            RetrievalChannel.EXACT: 20,
            RetrievalChannel.ALIAS: 20,
            RetrievalChannel.LEXICAL: 20,
            RetrievalChannel.DENSE: 20,
            RetrievalChannel.RELATION: 20,
        },
        fusion="rrf",
        rrf_k=60,
        rerank_limit=20,
        result_limit=5,
        requires_slot_decision=True,
        allows_executable_assets=True,
    ),
    RetrievalProfileName.SQL_EXEMPLAR: RetrievalProfileDefinition(
        name=RetrievalProfileName.SQL_EXEMPLAR,
        version="sql-exemplar-v1",
        allowed_sources=(RetrievalSourceType.SQL_EXEMPLAR,),
        allowed_resource_types=(RetrievalResourceType.SQL_EXEMPLAR,),
        channels=(RetrievalChannel.LEXICAL, RetrievalChannel.DENSE),
        recall_limits={RetrievalChannel.LEXICAL: 20, RetrievalChannel.DENSE: 20},
        fusion="rrf",
        rrf_k=60,
        rerank_limit=20,
        result_limit=5,
        requires_slot_decision=False,
        allows_executable_assets=False,
    ),
    RetrievalProfileName.KNOWLEDGE_EVIDENCE: RetrievalProfileDefinition(
        name=RetrievalProfileName.KNOWLEDGE_EVIDENCE,
        version="knowledge-evidence-v1",
        allowed_sources=(RetrievalSourceType.KNOWLEDGE_BASE,),
        allowed_resource_types=(RetrievalResourceType.KNOWLEDGE_CHUNK,),
        channels=(RetrievalChannel.LEXICAL, RetrievalChannel.DENSE),
        recall_limits={RetrievalChannel.LEXICAL: 40, RetrievalChannel.DENSE: 40},
        fusion="rrf",
        rrf_k=60,
        rerank_limit=20,
        result_limit=10,
        requires_slot_decision=False,
        allows_executable_assets=False,
    ),
    RetrievalProfileName.SCHEMA_FALLBACK: RetrievalProfileDefinition(
        name=RetrievalProfileName.SCHEMA_FALLBACK,
        version="schema-fallback-v1",
        allowed_sources=(RetrievalSourceType.SCHEMA,),
        allowed_resource_types=(RetrievalResourceType.TABLE, RetrievalResourceType.FIELD),
        channels=(RetrievalChannel.EXACT, RetrievalChannel.LEXICAL),
        recall_limits={RetrievalChannel.EXACT: 20, RetrievalChannel.LEXICAL: 20},
        fusion="rrf",
        rrf_k=60,
        rerank_limit=0,
        result_limit=10,
        requires_slot_decision=False,
        allows_executable_assets=False,
    ),
}


def get_retrieval_profile(name: RetrievalProfileName) -> RetrievalProfileDefinition:
    """读取已注册 profile；配置缺失时抛出明确领域错误。"""

    try:
        return PROFILE_REGISTRY[name]
    except KeyError as exc:
        raise RetrievalConfigurationError(f"未注册检索 profile: {name}") from exc
