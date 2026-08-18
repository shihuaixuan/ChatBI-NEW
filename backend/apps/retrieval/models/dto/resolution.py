"""R1 复合指标解析结果的严格契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """复合指标 DTO 使用严格字段边界，避免误把资产信息写入 hint。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

EvidenceLevel = Literal[
    "exact",
    "alias",
    "rerank",
    "lexical",
    "dense",
    "constraint_resolved",
]


class RatioOperand(_StrictModel):
    """比率的一个已绑定操作数。"""

    text: str = Field(min_length=1)
    asset_id: int = Field(gt=0)
    evidence: EvidenceLevel


class RatioSpec(_StrictModel):
    """由认证资产定义或拆解假设落地的比率表达。"""

    source_mention_id: str = Field(min_length=1)
    origin: Literal["asset_definition", "decomposition_hint"]
    numerator: RatioOperand
    denominator: RatioOperand


class CompositeMetricResolution(_StrictModel):
    """单个复合指标的确定性解析结果。"""

    mention_id: str = Field(min_length=1)
    status: Literal["asset_resolved", "ratio_resolved", "unresolved", "unsupported"]
    reason_code: str | None = None
    message: str | None = None
    ratio_spec: RatioSpec | None = None
    suggested_queries: tuple[str, ...] = ()


__all__ = [
    "CompositeMetricResolution",
    "EvidenceLevel",
    "RatioOperand",
    "RatioSpec",
]
