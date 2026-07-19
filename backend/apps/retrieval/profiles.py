"""检索 profile 定义。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.retrieval.errors import RetrievalConfigurationError
from apps.retrieval.models.dto import (
    RetrievalChannel,
    RetrievalProfileName,
    RetrievalResourceType,
    RetrievalSourceType,
)


class SemanticBindingGateThreshold(BaseModel):
    """一个语义槽位类型的可校准门控参数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_lexical_score: float = Field(ge=0, le=1)
    min_dense_score: float = Field(ge=-1, le=1)
    min_rerank_score: float = Field(ge=0, le=1)
    min_top_gap: float = Field(ge=0, le=1)


class SemanticBindingPolicyConfig(BaseModel):
    """semantic_binding 的重排与决策配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    thresholds: dict[RetrievalResourceType, SemanticBindingGateThreshold]

    @model_validator(mode="after")
    def validate_required_thresholds(self) -> SemanticBindingPolicyConfig:
        required_types = {
            RetrievalResourceType.METRIC,
            RetrievalResourceType.DIMENSION,
            RetrievalResourceType.VALUE,
            RetrievalResourceType.TERM,
        }
        if set(self.thresholds) != required_types:
            raise ValueError(
                "semantic_binding policy 必须为全部语义资产类型配置门控参数"
            )
        return self


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
    semantic_binding_policy: SemanticBindingPolicyConfig | None = None

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
        if self.requires_slot_decision and self.semantic_binding_policy is None:
            raise ValueError("需要槽位决策的 profile 必须配置 semantic_binding_policy")
        if not self.requires_slot_decision and self.semantic_binding_policy is not None:
            raise ValueError("不需要槽位决策的 profile 不能配置 semantic_binding_policy")
        return self


PROFILE_REGISTRY: dict[RetrievalProfileName, RetrievalProfileDefinition] = {
    RetrievalProfileName.SEMANTIC_BINDING: RetrievalProfileDefinition(
        name=RetrievalProfileName.SEMANTIC_BINDING,
        version="semantic-binding",
        allowed_sources=(RetrievalSourceType.SEMANTIC,),
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
        semantic_binding_policy=SemanticBindingPolicyConfig(
            version="semantic-binding-policy",
            thresholds={
                RetrievalResourceType.METRIC: SemanticBindingGateThreshold(
                    min_lexical_score=0.60,
                    min_dense_score=0.72,
                    min_rerank_score=0.72,
                    min_top_gap=0.08,
                ),
                RetrievalResourceType.DIMENSION: SemanticBindingGateThreshold(
                    min_lexical_score=0.60,
                    min_dense_score=0.72,
                    min_rerank_score=0.72,
                    min_top_gap=0.08,
                ),
                RetrievalResourceType.VALUE: SemanticBindingGateThreshold(
                    min_lexical_score=0.78,
                    min_dense_score=0.82,
                    min_rerank_score=0.82,
                    min_top_gap=0.10,
                ),
                RetrievalResourceType.TERM: SemanticBindingGateThreshold(
                    min_lexical_score=0.50,
                    min_dense_score=0.68,
                    min_rerank_score=0.68,
                    min_top_gap=0.06,
                ),
            },
        ),
    ),
    RetrievalProfileName.SQL_EXEMPLAR: RetrievalProfileDefinition(
        name=RetrievalProfileName.SQL_EXEMPLAR,
        version="sql-exemplar",
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
        version="knowledge-evidence",
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
        version="schema-fallback",
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
