"""统一检索请求、命中与决策契约。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """检索边界 DTO 的统一严格配置。"""

    model_config = ConfigDict(extra="forbid")


class RetrievalProfileName(str, Enum):
    """检索目标决定召回通道、排序策略和安全门控。"""

    SEMANTIC_BINDING = "semantic_binding"
    SQL_EXEMPLAR = "sql_exemplar"
    KNOWLEDGE_EVIDENCE = "knowledge_evidence"
    SCHEMA_FALLBACK = "schema_fallback"


class RetrievalPurpose(str, Enum):
    """子查询用途。"""

    METRIC = "metric"
    DIMENSION = "dimension"
    VALUE = "value"
    TERM = "term"
    EXEMPLAR = "exemplar"
    EVIDENCE = "evidence"
    SCHEMA = "schema"


class RetrievalDecisionStatus(str, Enum):
    """检索层稳定机器状态，展示文案由调用方映射。"""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    PARTIAL = "partial"
    MISSED = "missed"
    CROSS_MODEL = "cross_model"
    DEGRADED = "degraded"


class RetrievalChannel(str, Enum):
    """可独立观测的召回与排序通道。"""

    EXACT = "exact"
    ALIAS = "alias"
    LEXICAL = "lexical"
    DENSE = "dense"
    RELATION = "relation"
    RERANK = "rerank"


class RetrievalChannelStatus(str, Enum):
    """单个检索通道的执行状态。"""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class RetrievalResourceType(str, Enum):
    """统一检索资源类型。"""

    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    VALUE = "VALUE"
    TERM = "TERM"
    MODEL = "MODEL"
    DATASET = "DATASET"
    TABLE = "TABLE"
    FIELD = "FIELD"
    SQL_EXEMPLAR = "SQL_EXEMPLAR"
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"


class RetrievalSourceType(str, Enum):
    """检索资源来源。"""

    HEADLESS = "headless"
    SQL_EXEMPLAR = "sql_exemplar"
    KNOWLEDGE_BASE = "knowledge_base"
    SCHEMA = "schema"


class RetrievalDimensionSlot(_StrictModel):
    """问题理解投影到检索边界的维度槽位。"""

    name: str = Field(min_length=1)
    role: Literal["group_by", "filter", "ambiguous"]
    value: str | int | float | bool | None = None
    value_status: Literal["provided", "not_provided", "ambiguous"] = "not_provided"


class RetrievalIntent(_StrictModel):
    """检索所需的问题理解子集，不包含资产 ID。"""

    intent_type: str = Field(min_length=1)
    metric_mentions: list[str] = Field(default_factory=list)
    dimension_mentions: list[str] = Field(default_factory=list)
    dimension_slots: list[RetrievalDimensionSlot] = Field(default_factory=list)
    time_mentions: list[str] = Field(default_factory=list)
    time_range: dict[str, Any] = Field(default_factory=dict)
    filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    required_slot_types: list[str] = Field(default_factory=list)
    query_shape: dict[str, Any] = Field(default_factory=dict)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)
    subject_domain: dict[str, Any] = Field(default_factory=dict)


class RetrievalScope(_StrictModel):
    """经过应用层授权后传入的检索范围。"""

    dataset_ids: list[int] = Field(default_factory=list)
    knowledge_base_ids: list[int] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    principal_roles: list[str] = Field(default_factory=list)
    principal_role_ids: list[int] = Field(default_factory=list)
    permission_version: str | None = None

    @model_validator(mode="after")
    def validate_positive_ids(self) -> RetrievalScope:
        if any(
            value <= 0
            for value in [*self.dataset_ids, *self.knowledge_base_ids, *self.principal_role_ids]
        ):
            raise ValueError("检索 scope 中的 ID 必须为正整数")
        if any(not value.strip() for value in [*self.source_ids, *self.principal_roles]):
            raise ValueError("检索 scope 中的来源和角色不能为空字符串")
        return self


class RetrievalRequest(_StrictModel):
    """Graph 与 Agent 共用的统一检索请求。"""

    request_id: str = Field(min_length=1)
    tenant_id: int = Field(gt=0)
    actor_id: int = Field(gt=0)
    original_question: str = Field(min_length=1)
    rewritten_question: str = Field(min_length=1)
    intent: RetrievalIntent
    inherited_context: dict[str, Any] = Field(default_factory=dict)
    scope: RetrievalScope = Field(default_factory=RetrievalScope)
    profiles: list[RetrievalProfileName] = Field(min_length=1)
    strategy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_profiles(self) -> RetrievalRequest:
        if len(self.profiles) != len(set(self.profiles)):
            raise ValueError("检索 profile 不允许重复")
        return self


class RetrievalSubQuery(_StrictModel):
    """QueryPlanner 生成的单槽检索查询。"""

    subquery_id: str = Field(min_length=1)
    purpose: RetrievalPurpose
    text: str = Field(min_length=1)
    role: Literal["group_by", "filter", "ambiguous"] | None = None
    required: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)


class AssetReference(_StrictModel):
    """命中结果中的稳定 Headless 资产引用。"""

    asset_type: RetrievalResourceType
    asset_id: int = Field(gt=0)
    model_id: int | None = Field(default=None, gt=0)


class ExecutableAssetReference(_StrictModel):
    """允许进入 SQL 编译器的资产引用。"""

    asset_type: Literal[RetrievalResourceType.METRIC, RetrievalResourceType.DIMENSION]
    asset_id: int = Field(gt=0)
    model_id: int | None = Field(default=None, gt=0)


class RetrievalScores(_StrictModel):
    """保留各通道原始分数，禁止把不同量纲伪装成同一置信度。"""

    exact: float | None = None
    alias: float | None = None
    lexical: float | None = None
    dense: float | None = None
    rerank: float | None = None
    final: float | None = None


class RetrievalHit(_StrictModel):
    """可追溯到资源、检索单元和来源版本的单个命中。"""

    resource_id: str = Field(min_length=1)
    resource_type: RetrievalResourceType
    source_type: RetrievalSourceType
    source_id: str = Field(min_length=1)
    source_resource_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    content_kind: str = Field(min_length=1)
    title: str = Field(min_length=1)
    snippet: str = ""
    scores: RetrievalScores = Field(default_factory=RetrievalScores)
    ranks_by_channel: dict[RetrievalChannel, int] = Field(default_factory=dict)
    matched_field: str | None = None
    matched_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_version: str = Field(min_length=1)
    asset_ref: AssetReference | None = None

    @model_validator(mode="after")
    def validate_channel_ranks(self) -> RetrievalHit:
        if any(rank <= 0 for rank in self.ranks_by_channel.values()):
            raise ValueError("检索通道排名必须从 1 开始")
        return self


class RetrievalBindings(_StrictModel):
    """语义资产与 schema 候选，按业务类型保持独立顺序。"""

    metrics: list[RetrievalHit] = Field(default_factory=list)
    dimensions: list[RetrievalHit] = Field(default_factory=list)
    values: list[RetrievalHit] = Field(default_factory=list)
    terms: list[RetrievalHit] = Field(default_factory=list)
    models: list[RetrievalHit] = Field(default_factory=list)
    schema_hits: list[RetrievalHit] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_group_types(self) -> RetrievalBindings:
        expected = {
            "metrics": {RetrievalResourceType.METRIC},
            "dimensions": {RetrievalResourceType.DIMENSION},
            "values": {RetrievalResourceType.VALUE},
            "terms": {RetrievalResourceType.TERM},
            "models": {RetrievalResourceType.MODEL, RetrievalResourceType.DATASET},
            "schema_hits": {RetrievalResourceType.TABLE, RetrievalResourceType.FIELD},
        }
        for group_name, resource_types in expected.items():
            for hit in getattr(self, group_name):
                if hit.resource_type not in resource_types:
                    raise ValueError(f"{group_name} 包含了错误的资源类型 {hit.resource_type.value}")
        return self

    def all_hits(self) -> list[RetrievalHit]:
        """按稳定分组顺序返回全部绑定候选。"""

        return [
            *self.metrics,
            *self.dimensions,
            *self.values,
            *self.terms,
            *self.models,
            *self.schema_hits,
        ]


class RetrievalAmbiguity(_StrictModel):
    """需要澄清的槽位及候选。"""

    subquery_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    candidate_assets: list[AssetReference] = Field(default_factory=list)


class RetrievalSlotDecision(_StrictModel):
    """单个语义槽位的候选和最终决策。"""

    subquery_id: str = Field(min_length=1)
    purpose: RetrievalPurpose
    status: RetrievalDecisionStatus
    candidate_assets: list[AssetReference] = Field(default_factory=list)
    selected_assets: list[AssetReference] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selected_assets(self) -> RetrievalSlotDecision:
        candidate_keys = {_asset_key(item) for item in self.candidate_assets}
        selected_keys = {_asset_key(item) for item in self.selected_assets}
        if not selected_keys.issubset(candidate_keys):
            raise ValueError("槽位选中资产必须来自该槽位候选")
        return self


class RetrievalDecision(_StrictModel):
    """统一检索决策和 SQL 编译白名单。"""

    status: RetrievalDecisionStatus
    slot_decisions: list[RetrievalSlotDecision] = Field(default_factory=list)
    ambiguities: list[RetrievalAmbiguity] = Field(default_factory=list)
    allowed_asset_ids: list[ExecutableAssetReference] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_slots_and_assets(self) -> RetrievalDecision:
        slot_ids = [item.subquery_id for item in self.slot_decisions]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("槽位决策的 subquery_id 不允许重复")
        asset_keys = [_asset_key(item) for item in self.allowed_asset_ids]
        if len(asset_keys) != len(set(asset_keys)):
            raise ValueError("allowed_asset_ids 不允许重复")
        selected_keys = {
            _asset_key(asset)
            for slot in self.slot_decisions
            for asset in slot.selected_assets
        }
        if not set(asset_keys).issubset(selected_keys):
            raise ValueError("allowed_asset_ids 必须来自槽位最终选中资产")
        return self


class RetrievalChannelDiagnostic(_StrictModel):
    """单个通道执行诊断。"""

    channel: RetrievalChannel
    status: RetrievalChannelStatus
    latency_ms: float = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_error_code(self) -> RetrievalChannelDiagnostic:
        failed_statuses = {RetrievalChannelStatus.UNAVAILABLE, RetrievalChannelStatus.FAILED}
        if self.status in failed_statuses and not self.error_code:
            raise ValueError("不可用或失败的检索通道必须提供 error_code")
        if self.status == RetrievalChannelStatus.SUCCEEDED and self.error_code:
            raise ValueError("成功的检索通道不能携带 error_code")
        return self


class RetrievalDiagnostics(_StrictModel):
    """结果复现、性能分析和显式降级所需诊断。"""

    strategy_version: str = Field(min_length=1)
    index_generation: str = Field(min_length=1)
    channels: list[RetrievalChannelDiagnostic] = Field(default_factory=list)
    total_latency_ms: float = Field(default=0, ge=0)
    degraded_reason: str | None = None

    @model_validator(mode="after")
    def validate_unique_channels(self) -> RetrievalDiagnostics:
        channels = [item.channel for item in self.channels]
        if len(channels) != len(set(channels)):
            raise ValueError("每个检索通道只能有一条诊断")
        return self


class RetrievalBundle(_StrictModel):
    """Graph 与 Agent 共用的统一检索结果。"""

    request_id: str = Field(min_length=1)
    bindings: RetrievalBindings = Field(default_factory=RetrievalBindings)
    exemplars: list[RetrievalHit] = Field(default_factory=list)
    evidence: list[RetrievalHit] = Field(default_factory=list)
    decision: RetrievalDecision
    diagnostics: RetrievalDiagnostics

    @model_validator(mode="after")
    def validate_result_boundaries(self) -> RetrievalBundle:
        for hit in self.exemplars:
            if hit.source_type != RetrievalSourceType.SQL_EXEMPLAR:
                raise ValueError("exemplars 只能包含 SQL_EXEMPLAR 来源")
        for hit in self.evidence:
            if hit.source_type != RetrievalSourceType.KNOWLEDGE_BASE:
                raise ValueError("evidence 只能包含 KNOWLEDGE_BASE 来源")

        binding_asset_keys = {
            _asset_key(hit.asset_ref)
            for hit in self.bindings.all_hits()
            if hit.asset_ref is not None
        }
        allowed_asset_keys = {_asset_key(item) for item in self.decision.allowed_asset_ids}
        if not allowed_asset_keys.issubset(binding_asset_keys):
            raise ValueError("allowed_asset_ids 必须是语义绑定候选的子集")

        if self.decision.status == RetrievalDecisionStatus.DEGRADED and not self.diagnostics.degraded_reason:
            raise ValueError("degraded 结果必须提供 degraded_reason")
        return self


def _asset_key(asset: AssetReference | ExecutableAssetReference) -> tuple[str, int, int | None]:
    return (str(asset.asset_type.value), asset.asset_id, asset.model_id)
