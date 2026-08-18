"""复合比率的确定性方向校验。

该模块不根据指标名称猜测分子、分母。方向只能来自语义资产契约提供的
复合指标顺序，或来自运行时快照已经确认的 operand 角色。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from apps.retrieval.models.dto import (
    AssetReference,
    CompositeMetricResolution,
    RatioOperand,
    RatioSpec,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalRequest,
)
from apps.semantic.models.dto import DatasetSchema, SchemaElement

RatioOperandRole = Literal["numerator", "denominator", "unknown"]


class RatioDirectionError(ValueError):
    """比率方向无法由语义资产契约证明。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(f"{code}:{message}" if message else code)


@dataclass(frozen=True, slots=True)
class RatioOperandCandidate:
    """比率操作数的绑定结果；role 必须来自语义资产运行时快照。"""

    asset_id: int
    model_id: int
    text: str
    role: RatioOperandRole = "unknown"

    def __post_init__(self) -> None:
        if self.asset_id <= 0 or self.model_id <= 0:
            raise ValueError("RATIO_OPERAND_ASSET_ID_INVALID")
        if not self.text.strip():
            raise ValueError("RATIO_OPERAND_TEXT_REQUIRED")


@dataclass(frozen=True, slots=True)
class RatioDirectionResult:
    """通过方向校验后的稳定结果。"""

    numerator: RatioOperandCandidate
    denominator: RatioOperandCandidate
    evidence: Literal["asset_definition", "operand_roles"]


@dataclass(frozen=True, slots=True)
class CompositeResolutionReport:
    """复合指标解析的批量结果，供检索投影和 Trace 同时消费。"""

    resolutions: tuple[CompositeMetricResolution, ...]
    ratio_specs: tuple[RatioSpec, ...]

    @property
    def has_failure(self) -> bool:
        return any(item.status in {"unresolved", "unsupported"} for item in self.resolutions)


def validate_ratio_direction(
    numerator: RatioOperandCandidate,
    denominator: RatioOperandCandidate,
    *,
    declared_order: tuple[int, int] | None = None,
) -> RatioDirectionResult:
    """验证比率分子、分母方向并返回可编译的结果。

    ``declared_order`` 是复合指标资产定义中的有序 metric_refs。没有资产定义时，
    只能使用运行时快照提供的 operand role；两者都不能证明方向时固定拒绝。
    """

    if numerator.model_id != denominator.model_id:
        raise RatioDirectionError(
            "RATIO_CROSS_MODEL_UNSUPPORTED",
            "比率分子和分母属于不同语义模型",
        )
    if numerator.asset_id == denominator.asset_id:
        raise RatioDirectionError(
            "RATIO_OPERANDS_IDENTICAL",
            "比率分子和分母不能引用同一指标资产",
        )

    if declared_order is not None:
        if declared_order != (numerator.asset_id, denominator.asset_id):
            raise RatioDirectionError(
                "RATIO_DIRECTION_MISMATCH",
                "绑定的分子分母顺序与语义资产定义不一致",
            )
        return RatioDirectionResult(
            numerator=numerator,
            denominator=denominator,
            evidence="asset_definition",
        )

    roles = (numerator.role, denominator.role)
    if roles == ("numerator", "denominator"):
        return RatioDirectionResult(
            numerator=numerator,
            denominator=denominator,
            evidence="operand_roles",
        )
    if roles == ("denominator", "numerator"):
        raise RatioDirectionError(
            "RATIO_DIRECTION_MISMATCH",
            "模型绑定结果把分子和分母顺序反置",
        )
    raise RatioDirectionError(
        "RATIO_DIRECTION_UNPROVEN",
        "语义资产没有提供可证明的分子分母方向",
    )


def resolve_composite_metrics(
    request: RetrievalRequest,
    initial_bundle: RetrievalBundle,
    decomposition_bundle: RetrievalBundle | None,
    schema: DatasetSchema,
) -> CompositeResolutionReport:
    """按整体资产优先、拆解检索其次的顺序解析复合指标。

    该函数只消费检索候选和语义 Schema，不根据中文名称猜测分子分母。
    拆解 hint 只能产生独立检索文本，最终资产必须通过检索门控和方向事实校验。
    """

    graph = request.intent.mention_graph
    if not isinstance(graph, dict):
        return CompositeResolutionReport((), ())
    mentions = graph.get("mentions")
    if not isinstance(mentions, list):
        return CompositeResolutionReport((), ())
    metric_mentions = [
        item
        for item in mentions
        if isinstance(item, dict)
        and item.get("kind") == "metric_phrase"
        and item.get("metric_role") != "computed"
    ]
    composite_mentions = [
        item for item in metric_mentions if item.get("metric_role") == "composite_unknown"
    ]
    if not composite_mentions:
        return CompositeResolutionReport((), ())

    metric_slot_ids = {
        str(item.get("mention_id")): f"metric:{index}"
        for index, item in enumerate(metric_mentions, start=1)
    }
    schema_metrics = {item.id: item for item in schema.metrics}
    initial_slots = {
        item.subquery_id: item
        for item in initial_bundle.decision.slot_decisions
        if item.purpose.value == "metric"
    }
    decomposition_slots = {
        item.subquery_id: item
        for item in (decomposition_bundle.decision.slot_decisions if decomposition_bundle else ())
        if item.purpose.value == "metric"
    }
    initial_hits = _hits_by_asset(initial_bundle)
    decomposition_hits = _hits_by_asset(decomposition_bundle)
    resolutions: list[CompositeMetricResolution] = []
    ratio_specs: list[RatioSpec] = []

    for mention in composite_mentions:
        mention_id = str(mention.get("mention_id") or "").strip()
        full_slot = initial_slots.get(metric_slot_ids.get(mention_id, ""))
        full_asset = _single_selected_asset(full_slot)
        if full_asset is not None:
            element = schema_metrics.get(full_asset.asset_id)
            refs = _metric_refs(element) if element is not None else ()
            if len(refs) == 2 and all(ref in schema_metrics for ref in refs):
                numerator = RatioOperand(
                    text=_metric_text(schema_metrics[refs[0]]),
                    asset_id=refs[0],
                    evidence="constraint_resolved",
                )
                denominator = RatioOperand(
                    text=_metric_text(schema_metrics[refs[1]]),
                    asset_id=refs[1],
                    evidence="constraint_resolved",
                )
                ratio = RatioSpec(
                    source_mention_id=mention_id,
                    origin="asset_definition",
                    numerator=numerator,
                    denominator=denominator,
                )
                ratio_specs.append(ratio)
                resolutions.append(
                    CompositeMetricResolution(
                        mention_id=mention_id,
                        status="ratio_resolved",
                        ratio_spec=ratio,
                    )
                )
            else:
                # 整短语已经命中普通认证指标，按普通指标继续，不再猜测表达式。
                resolutions.append(
                    CompositeMetricResolution(
                        mention_id=mention_id,
                        status="asset_resolved",
                    )
                )
            continue

        decomposition = mention.get("decomposition")
        numerator_text = str((decomposition or {}).get("numerator_text") or "").strip()
        denominator_text = str((decomposition or {}).get("denominator_text") or "").strip()
        if not numerator_text or not denominator_text or decomposition_bundle is None:
            resolutions.append(
                _unresolved_resolution(
                    mention_id,
                    mention_text=str(mention.get("text") or "复合指标"),
                    numerator_text=numerator_text,
                    denominator_text=denominator_text,
                )
            )
            continue

        numerator_slot = decomposition_slots.get(f"ratio:{mention_id}:numerator")
        denominator_slot = decomposition_slots.get(f"ratio:{mention_id}:denominator")
        numerator_asset = _single_selected_asset(numerator_slot)
        denominator_asset = _single_selected_asset(denominator_slot)
        if numerator_asset is None or denominator_asset is None:
            resolutions.append(
                _unresolved_resolution(
                    mention_id,
                    mention_text=str(mention.get("text") or "复合指标"),
                    numerator_text=numerator_text,
                    denominator_text=denominator_text,
                )
            )
            continue

        numerator_hit = initial_hits.get((numerator_asset.asset_id, numerator_asset.model_id)) or decomposition_hits.get(
            (numerator_asset.asset_id, numerator_asset.model_id)
        )
        denominator_hit = initial_hits.get((denominator_asset.asset_id, denominator_asset.model_id)) or decomposition_hits.get(
            (denominator_asset.asset_id, denominator_asset.model_id)
        )
        numerator = RatioOperand(
            text=numerator_text,
            asset_id=numerator_asset.asset_id,
            evidence=_evidence_for_hit(numerator_hit),
        )
        denominator = RatioOperand(
            text=denominator_text,
            asset_id=denominator_asset.asset_id,
            evidence=_evidence_for_hit(denominator_hit),
        )
        try:
            validate_ratio_direction(
                RatioOperandCandidate(
                    asset_id=numerator_asset.asset_id,
                    model_id=numerator_asset.model_id or 0,
                    text=numerator_text,
                    role=_operand_role(numerator_hit),
                ),
                RatioOperandCandidate(
                    asset_id=denominator_asset.asset_id,
                    model_id=denominator_asset.model_id or 0,
                    text=denominator_text,
                    role=_operand_role(denominator_hit),
                ),
            )
        except RatioDirectionError as exc:
            resolutions.append(
                CompositeMetricResolution(
                    mention_id=mention_id,
                    status="unsupported" if exc.code == "RATIO_CROSS_MODEL_UNSUPPORTED" else "unresolved",
                    reason_code=exc.code,
                    message=_failure_message(
                        exc.code,
                        str(mention.get("text") or "复合指标"),
                        numerator_text,
                        denominator_text,
                    ),
                    suggested_queries=(numerator_text, denominator_text),
                )
            )
            continue

        ratio = RatioSpec(
            source_mention_id=mention_id,
            origin="decomposition_hint",
            numerator=numerator,
            denominator=denominator,
        )
        ratio_specs.append(ratio)
        resolutions.append(
            CompositeMetricResolution(
                mention_id=mention_id,
                status="ratio_resolved",
                ratio_spec=ratio,
            )
        )

    return CompositeResolutionReport(tuple(resolutions), tuple(ratio_specs))


def _single_selected_asset(slot: Any) -> AssetReference | None:
    if slot is None or slot.status != RetrievalDecisionStatus.RESOLVED:
        return None
    return slot.selected_assets[0] if len(slot.selected_assets) == 1 else None


def _hits_by_asset(bundle: RetrievalBundle | None) -> dict[tuple[int, int | None], Any]:
    if bundle is None:
        return {}
    return {
        (hit.asset_ref.asset_id, hit.asset_ref.model_id): hit
        for hit in bundle.bindings.metrics
        if hit.asset_ref is not None
    }


def _metric_refs(element: SchemaElement | None) -> tuple[int, ...]:
    if element is None:
        return ()
    params = element.type_params or {}
    metric_params = params.get("metricDefineByMetricParams") or {}
    refs = [
        item.get("id")
        for item in metric_params.get("metrics") or []
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]
    if not refs and isinstance(element.ext_info, dict):
        refs = [item for item in element.ext_info.get("metric_refs") or [] if isinstance(item, int)]
    return tuple(dict.fromkeys(item for item in refs if item > 0))


def _metric_text(element: SchemaElement) -> str:
    return str(element.name or element.biz_name)


def _evidence_for_hit(hit: Any) -> str:
    if hit is None:
        return "constraint_resolved"
    if hit.scores.exact is not None:
        return "exact"
    if hit.scores.alias is not None:
        return "alias"
    if hit.scores.rerank is not None:
        return "rerank"
    if hit.scores.lexical is not None:
        return "lexical"
    if hit.scores.dense is not None:
        return "dense"
    return "constraint_resolved"


def _operand_role(hit: Any) -> RatioOperandRole:
    if hit is None:
        return "unknown"
    for source in (hit.metadata, hit.provenance):
        if not isinstance(source, dict):
            continue
        value = str(source.get("operand_role") or source.get("ratio_role") or "").lower()
        if value in {"numerator", "denominator"}:
            return value  # type: ignore[return-value]
    return "unknown"


def _unresolved_resolution(
    mention_id: str,
    *,
    mention_text: str,
    numerator_text: str,
    denominator_text: str,
) -> CompositeMetricResolution:
    suggestions = tuple(item for item in (numerator_text, denominator_text) if item)
    return CompositeMetricResolution(
        mention_id=mention_id,
        status="unresolved",
        reason_code="COMPOSITE_METRIC_UNRESOLVED",
        message=(
            f"未找到『{mention_text}』的认证口径，可分别查询"
            f"『{numerator_text}』『{denominator_text}』"
            if numerator_text and denominator_text
            else f"未找到『{mention_text}』的认证口径"
        ),
        suggested_queries=suggestions,
    )


def _failure_message(
    code: str,
    mention_text: str,
    numerator_text: str,
    denominator_text: str,
) -> str:
    if code == "RATIO_CROSS_MODEL_UNSUPPORTED":
        return "该口径的分子分母分属不同语义模型，当前不支持跨模型比率，建议分别查询"
    if code == "COMPOSITE_METRIC_UNRESOLVED":
        return (
            f"未找到『{mention_text}』的认证口径，可分别查询"
            f"『{numerator_text}』『{denominator_text}』"
        )
    return "复合指标的分子分母方向无法由语义资产证明，无法安全计算"


__all__ = [
    "CompositeResolutionReport",
    "RatioDirectionError",
    "RatioDirectionResult",
    "RatioOperandCandidate",
    "RatioOperandRole",
    "validate_ratio_direction",
    "resolve_composite_metrics",
]
