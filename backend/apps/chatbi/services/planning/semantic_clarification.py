"""从语义检索快照生成可验证的澄清选项。"""

from __future__ import annotations

from itertools import product
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval import (
    AssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalPurpose,
    RetrievalQueryError,
    RetrievalResourceType,
    SemanticClarificationBinding,
)


class SemanticClarificationOption(BaseModel):
    """由服务端候选生成的单个澄清选项。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    asset_id: int | None = None
    bindings: list[SemanticClarificationBinding] = Field(default_factory=list)


class SemanticClarification(BaseModel):
    """语义歧义的服务层澄清结果。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    options: list[SemanticClarificationOption] = Field(min_length=1)


def build_semantic_clarification(
    state: dict[str, Any],
) -> SemanticClarification | None:
    """从服务端权威歧义快照生成澄清参数。"""

    raw_bundle = state.get("semantic_bundle")
    if not isinstance(raw_bundle, dict):
        return None
    bundle = RetrievalBundle.model_validate(raw_bundle)
    if bundle.decision.status != RetrievalDecisionStatus.AMBIGUOUS:
        return None

    candidate_details = semantic_candidate_details(state)
    options: list[SemanticClarificationOption] = []
    ambiguity_names: list[str] = []
    ambiguities_by_purpose = {
        purpose: [
            ambiguity
            for ambiguity in bundle.decision.ambiguities
            if ambiguity_purpose(bundle, ambiguity.subquery_id) == purpose
        ]
        for purpose in (RetrievalPurpose.METRIC, RetrievalPurpose.DIMENSION)
    }
    consumed_subquery_ids: set[str] = set()
    metric_ambiguities = ambiguities_by_purpose[RetrievalPurpose.METRIC]
    dimension_ambiguities = ambiguities_by_purpose[RetrievalPurpose.DIMENSION]
    if len(metric_ambiguities) == 1 and dimension_ambiguities:
        metric_ambiguity = metric_ambiguities[0]
        for metric in metric_ambiguity.candidate_assets:
            compatible_by_dimension_slot = [
                [
                    dimension
                    for dimension in ambiguity.candidate_assets
                    if metric_dimension_compatible(bundle, metric, dimension)
                ]
                for ambiguity in dimension_ambiguities
            ]
            if any(not candidates for candidates in compatible_by_dimension_slot):
                continue
            for dimensions in product(*compatible_by_dimension_slot):
                metric_name = candidate_display_name(candidate_details, metric)
                if metric_name not in ambiguity_names:
                    ambiguity_names.append(metric_name)
                bindings = [
                    SemanticClarificationBinding(
                        subquery_id=metric_ambiguity.subquery_id,
                        asset_type=RetrievalResourceType.METRIC,
                        asset_id=metric.asset_id,
                        model_id=metric.model_id,
                    ),
                    *[
                        SemanticClarificationBinding(
                            subquery_id=ambiguity.subquery_id,
                            asset_type=RetrievalResourceType.DIMENSION,
                            asset_id=dimension.asset_id,
                            model_id=dimension.model_id,
                        )
                        for ambiguity, dimension in zip(
                            dimension_ambiguities,
                            dimensions,
                            strict=True,
                        )
                    ],
                ]
                dimension_names = [
                    candidate_display_name(candidate_details, dimension)
                    for dimension in dimensions
                ]
                label = metric_name
                if len(compatible_by_dimension_slot) > 1 or any(
                    len(candidates) > 1 for candidates in compatible_by_dimension_slot
                ):
                    label = f"{metric_name}（{'、'.join(dimension_names)}）"
                options.append(
                    SemanticClarificationOption(
                        label=label,
                        value="|".join(
                            f"{binding.asset_type.value}:{binding.asset_id}:"
                            f"{binding.model_id or 0}"
                            for binding in bindings
                        ),
                        asset_id=metric.asset_id,
                        bindings=bindings,
                    )
                )
        if options:
            consumed_subquery_ids = {
                metric_ambiguity.subquery_id,
                *(item.subquery_id for item in dimension_ambiguities),
            }

    for ambiguity in bundle.decision.ambiguities:
        if ambiguity.subquery_id in consumed_subquery_ids:
            continue
        base_names = [
            candidate_display_name(candidate_details, asset)
            for asset in ambiguity.candidate_assets
        ]
        duplicate_names = {
            name for name in base_names if base_names.count(name) > 1
        }
        for index, asset in enumerate(ambiguity.candidate_assets, start=1):
            name = candidate_display_name(candidate_details, asset)
            if name not in ambiguity_names:
                ambiguity_names.append(name)
            label = name
            if name in duplicate_names:
                biz_name = str(
                    candidate_details.get(asset_key(asset), {}).get("biz_name") or ""
                ).strip()
                label = f"{name}（{biz_name or index}）"
            options.append(
                SemanticClarificationOption(
                    label=label,
                    value=(
                        f"{asset.asset_type.value}:{asset.asset_id}:"
                        f"{asset.model_id or 0}"
                    ),
                    asset_id=asset.asset_id,
                    bindings=[
                        SemanticClarificationBinding(
                            subquery_id=ambiguity.subquery_id,
                            asset_type=executable_asset_type(asset),
                            asset_id=asset.asset_id,
                            model_id=asset.model_id,
                        )
                    ],
                )
            )
    if not options:
        return None

    subject = "、".join(ambiguity_names) or "当前查询"
    return SemanticClarification(
        question=f"“{subject}”存在多个可执行口径，请选择本次要查询的口径。",
        options=options,
    )


def ambiguity_purpose(
    bundle: RetrievalBundle,
    subquery_id: str,
) -> RetrievalPurpose | None:
    return next(
        (
            decision.purpose
            for decision in bundle.decision.slot_decisions
            if decision.subquery_id == subquery_id
        ),
        None,
    )


def metric_dimension_compatible(
    bundle: RetrievalBundle,
    metric: AssetReference,
    dimension: AssetReference,
) -> bool:
    if metric.model_id is not None and metric.model_id == dimension.model_id:
        return True
    metric_hit = next(
        (
            hit
            for hit in bundle.bindings.metrics
            if hit.asset_ref is not None and asset_key(hit.asset_ref) == asset_key(metric)
        ),
        None,
    )
    if metric_hit is None:
        return False
    compatible_dimension_ids = {
        int(value)
        for value in metric_hit.metadata.get("compatible_dimension_ids", [])
        if isinstance(value, int) and value > 0
    }
    return dimension.asset_id in compatible_dimension_ids


def semantic_candidate_details(
    state: dict[str, Any],
) -> dict[tuple[str, int, int | None], dict[str, Any]]:
    payload = state.get("semantic_payload")
    if not isinstance(payload, dict):
        payload = state.get("semantic_package")
    groups = payload.get("candidate_groups") if isinstance(payload, dict) else None
    result: dict[tuple[str, int, int | None], dict[str, Any]] = {}
    if not isinstance(groups, dict):
        return result
    for items in groups.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            asset_type = str(item.get("asset_type") or "")
            asset_id = item.get("asset_id")
            model_id = item.get("model_id")
            if not isinstance(asset_id, int):
                continue
            key = (
                asset_type,
                asset_id,
                model_id if isinstance(model_id, int) else None,
            )
            result[key] = item
    return result


def semantic_candidate_names(
    state: dict[str, Any],
) -> dict[tuple[str, int, int | None], set[str]]:
    return {
        key: {
            normalized
            for value in (
                item.get("display_name"),
                item.get("name"),
                item.get("biz_name"),
                str(key[1]),
            )
            if (normalized := normalize_option_value(value))
        }
        for key, item in semantic_candidate_details(state).items()
    }


def candidate_display_name(
    candidate_details: dict[tuple[str, int, int | None], dict[str, Any]],
    asset: AssetReference,
) -> str:
    details = candidate_details.get(asset_key(asset), {})
    if str(details.get("asset_type") or "") == "VALUE":
        value_name = str(details.get("retrieval_title") or "").strip()
        if value_name:
            return value_name
    return str(
        details.get("display_name")
        or details.get("name")
        or details.get("biz_name")
        or details.get("retrieval_title")
        or f"业务口径 {asset.asset_id}"
    )


def ambiguous_candidates(
    bundle: RetrievalBundle,
) -> list[tuple[str, AssetReference]]:
    return [
        (ambiguity.subquery_id, asset)
        for ambiguity in bundle.decision.ambiguities
        for asset in ambiguity.candidate_assets
    ]


def asset_key(asset: AssetReference) -> tuple[str, int, int | None]:
    return asset.asset_type.value, asset.asset_id, asset.model_id


def executable_asset_type(
    asset: AssetReference,
) -> Literal[
    RetrievalResourceType.METRIC,
    RetrievalResourceType.DIMENSION,
    RetrievalResourceType.VALUE,
]:
    if asset.asset_type in {
        RetrievalResourceType.METRIC,
        RetrievalResourceType.DIMENSION,
        RetrievalResourceType.VALUE,
    }:
        return cast(
            Literal[
                RetrievalResourceType.METRIC,
                RetrievalResourceType.DIMENSION,
                RetrievalResourceType.VALUE,
            ],
            asset.asset_type,
        )
    raise RetrievalQueryError(
        "语义澄清候选不是可执行资产",
        details={
            "reason_code": "SEMANTIC_CLARIFICATION_ASSET_TYPE_NOT_EXECUTABLE",
            "asset_type": asset.asset_type.value,
            "asset_id": asset.asset_id,
        },
    )


def normalize_option_value(value: Any) -> str:
    return "".join(str(value or "").strip().casefold().split())


__all__ = [
    "SemanticClarification",
    "SemanticClarificationOption",
    "ambiguous_candidates",
    "asset_key",
    "build_semantic_clarification",
    "candidate_display_name",
    "executable_asset_type",
    "normalize_option_value",
    "semantic_candidate_details",
    "semantic_candidate_names",
]
