import math
import time
from typing import Any

from sqlalchemy import select

from apps.semantic.models.semantic_model import (
    AssetStatus,
    AssetType,
    SemanticDimension,
    SemanticMetric,
)
from apps.semantic.models.semantic_schema import (
    SemanticAssetMatch,
    SemanticRetrieveRequest,
    SemanticRetrieveResponse,
)
from common.core.deps import SessionDep

MIN_SEMANTIC_SCORE = 0.55
APPROVED_ASSET_CACHE_TTL_SECONDS = 300
EMPTY_APPROVED_ASSET_CACHE_TTL_SECONDS = 60
_approved_asset_cache: dict[tuple[int, int], tuple[float, list[SemanticMetric], list[SemanticDimension]]] = {}


def _value(asset: Any, key: str, default=None):
    if isinstance(asset, dict):
        return asset.get(key, default)
    return getattr(asset, key, default)


def _contains(question: str, value: str | None) -> bool:
    return bool(value and value.strip() and value.lower() in question.lower())


def _matched_word(question: str, asset) -> str | None:
    for key in ("display_name", "name"):
        value = _value(asset, key)
        if _contains(question, value):
            return value
    for alias in _value(asset, "aliases", []) or []:
        if _contains(question, alias):
            return alias
    description = _value(asset, "description")
    if _contains(question, description):
        return description
    return None


def alias_score(question: str, asset) -> float:
    if _contains(question, _value(asset, "display_name")):
        return 1.0
    if _contains(question, _value(asset, "name")):
        return 0.95
    for alias in _value(asset, "aliases", []) or []:
        if _contains(question, alias):
            return 0.9
    if _contains(question, _value(asset, "description")):
        return 0.35
    return 0.0


def embedding_score(question_embedding, asset_embedding) -> float:
    if question_embedding is None or asset_embedding is None:
        return 0.0
    question_vector = list(question_embedding)
    asset_vector = list(asset_embedding)
    if not question_vector or not asset_vector or len(question_vector) != len(asset_vector):
        return 0.0
    dot = sum(left * right for left, right in zip(question_vector, asset_vector, strict=True))
    question_norm = math.sqrt(sum(value * value for value in question_vector))
    asset_norm = math.sqrt(sum(value * value for value in asset_vector))
    if question_norm == 0 or asset_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (question_norm * asset_norm)))


def exact_name_score(question: str, asset) -> float:
    if _contains(question, _value(asset, "display_name")):
        return 1.0
    if _contains(question, _value(asset, "name")):
        return 1.0
    return 0.0


def usage_boost(_asset) -> float:
    return 0.0


def _hybrid_score(question: str, asset, question_embedding) -> float:
    return (
        0.45 * alias_score(question, asset)
        + 0.35 * embedding_score(question_embedding, _value(asset, "embedding"))
        + 0.10 * exact_name_score(question, asset)
        + 0.10 * usage_boost(asset)
    )


def _asset_snapshot(asset) -> dict[str, Any]:
    snapshot = {
        "id": asset.id,
        "name": asset.name,
        "display_name": asset.display_name,
        "aliases": asset.aliases or [],
        "description": asset.description,
        "expr": asset.expr,
        "status": asset.status,
    }
    if isinstance(asset, SemanticMetric):
        snapshot["default_agg"] = asset.default_agg
        snapshot["filter_sql"] = asset.filter_sql
    else:
        snapshot["dimension_type"] = asset.dimension_type
        snapshot["semantic_type"] = asset.semantic_type
        snapshot["time_granularities"] = asset.time_granularities or []
    return snapshot


def _match_asset(
    question: str,
    asset,
    asset_type: str,
    question_embedding=None,
    degraded: bool = False,
) -> SemanticAssetMatch | None:
    score = alias_score(question, asset) if degraded else _hybrid_score(question, asset, question_embedding)
    if score < MIN_SEMANTIC_SCORE:
        return None
    return SemanticAssetMatch(
        asset_type=asset_type,
        asset_id=asset.id,
        name=asset.name,
        display_name=asset.display_name,
        score=score,
        match_word=_matched_word(question, asset),
        snapshot=_asset_snapshot(asset),
    )


def _sort_and_limit(matches: list[SemanticAssetMatch], limit: int) -> list[SemanticAssetMatch]:
    return sorted(matches, key=lambda match: (-match.score, match.display_name, match.asset_id))[:limit]


def clear_semantic_asset_cache() -> None:
    _approved_asset_cache.clear()


def invalidate_semantic_asset_cache(oid: int, datasource_id: int) -> None:
    _approved_asset_cache.pop((oid, datasource_id), None)


def _load_approved_assets(session: SessionDep, oid: int, datasource_id: int) -> tuple[list[SemanticMetric], list[SemanticDimension]]:
    metrics = list(
        session.execute(
            select(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.status == AssetStatus.APPROVED.value,
            )
        )
        .scalars()
        .all()
    )
    dimensions = list(
        session.execute(
            select(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.datasource_id == datasource_id,
                SemanticDimension.status == AssetStatus.APPROVED.value,
            )
        )
        .scalars()
        .all()
    )
    return metrics, dimensions


def _get_cached_approved_assets(
    session: SessionDep,
    oid: int,
    datasource_id: int,
) -> tuple[list[SemanticMetric], list[SemanticDimension]]:
    cache_key = (oid, datasource_id)
    cached = _approved_asset_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None:
        expires_at, metrics, dimensions = cached
        if expires_at > now:
            return metrics, dimensions

    metrics, dimensions = _load_approved_assets(session, oid, datasource_id)
    ttl = APPROVED_ASSET_CACHE_TTL_SECONDS if metrics or dimensions else EMPTY_APPROVED_ASSET_CACHE_TTL_SECONDS
    _approved_asset_cache[cache_key] = (now + ttl, metrics, dimensions)
    return metrics, dimensions


def _embed_question(question: str, embedding_model=None):
    if embedding_model is None:
        return None, True, "embedding model unavailable"
    try:
        return embedding_model.embed_query(question), False, ""
    except Exception as exc:
        return None, True, str(exc)


def retrieve_semantic_assets(
    session: SessionDep,
    request: SemanticRetrieveRequest,
    embedding_model=None,
) -> SemanticRetrieveResponse:
    question_embedding, degraded, reason = _embed_question(request.question, embedding_model)
    metrics, dimensions = _get_cached_approved_assets(session, request.oid, request.datasource_id)

    metric_matches = [
        match
        for metric in metrics
        if (match := _match_asset(request.question, metric, AssetType.METRIC.value, question_embedding, degraded)) is not None
    ]
    dimension_matches = [
        match
        for dimension in dimensions
        if (
            match := _match_asset(request.question, dimension, AssetType.DIMENSION.value, question_embedding, degraded)
        )
        is not None
    ]
    return SemanticRetrieveResponse(
        metrics=_sort_and_limit(metric_matches, request.metric_top_k),
        dimensions=_sort_and_limit(dimension_matches, request.dimension_top_k),
        degraded=degraded,
        reason=reason,
    )
