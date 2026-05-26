import re
from typing import Any

from sqlalchemy import select

from apps.ai_model.embedding import EmbeddingModelCache
from apps.semantic.models.semantic_model import (
    AssetStatus,
    SemanticDimension,
    SemanticMetric,
)
from apps.semantic.services.semantic_observability import record_semantic_metric
from common.core.config import settings
from common.core.deps import SessionDep
from common.utils.embedding_threads import executor, session_maker
from common.utils.utils import SQLBotLogUtil

SENSITIVE_ASSIGNMENT = re.compile(r"\b(password|token|configuration)\s*=\s*\S+", re.IGNORECASE)
CONNECTION_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
semantic_embedding_failed_total = 0


def _value(asset: Any, key: str, default=None):
    if isinstance(asset, dict):
        return asset.get(key, default)
    return getattr(asset, key, default)


def _sanitize_text(text: str) -> str:
    text = CONNECTION_URL.sub("[REDACTED_DSN]", text)
    return SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def _append_line(lines: list[str], label: str, value) -> None:
    if value is None:
        return
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item)
    if not str(value).strip():
        return
    lines.append(f"{label}: {_sanitize_text(str(value).strip())}")


def build_metric_embedding_text(metric) -> str:
    lines: list[str] = []
    _append_line(lines, "display_name", _value(metric, "display_name"))
    _append_line(lines, "name", _value(metric, "name"))
    _append_line(lines, "aliases", _value(metric, "aliases", []))
    _append_line(lines, "description", _value(metric, "description"))
    _append_line(lines, "expr", _value(metric, "expr"))
    _append_line(lines, "default_agg", _value(metric, "default_agg"))
    return "\n".join(lines)


def build_dimension_embedding_text(dimension) -> str:
    lines: list[str] = []
    _append_line(lines, "display_name", _value(dimension, "display_name"))
    _append_line(lines, "name", _value(dimension, "name"))
    _append_line(lines, "aliases", _value(dimension, "aliases", []))
    _append_line(lines, "description", _value(dimension, "description"))
    _append_line(lines, "dimension_type", _value(dimension, "dimension_type"))
    _append_line(lines, "semantic_type", _value(dimension, "semantic_type"))
    _append_line(lines, "expr", _value(dimension, "expr"))
    return "\n".join(lines)


def _get_embedding_model(embedding_model=None):
    return embedding_model or EmbeddingModelCache.get_model()


def _record_embedding_failure(asset_type: str, asset_id: int, exc: Exception) -> None:
    global semantic_embedding_failed_total
    semantic_embedding_failed_total += 1
    record_semantic_metric(
        "semantic_embedding_failed_total",
        asset_type=asset_type,
        asset_id=asset_id,
        error=str(exc),
    )
    SQLBotLogUtil.error(f"semantic embedding rebuild failed: asset_type={asset_type} asset_id={asset_id}: {exc}")


def rebuild_metric_embedding(session: SessionDep, metric_id: int, embedding_model=None) -> bool:
    metric = session.get(SemanticMetric, metric_id)
    if metric is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    if embedding_model is None and not settings.EMBEDDING_ENABLED:
        return False
    try:
        embedding = _get_embedding_model(embedding_model).embed_query(build_metric_embedding_text(metric))
        metric.embedding = embedding
        session.add(metric)
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        _record_embedding_failure("METRIC", metric_id, exc)
        return False


def rebuild_dimension_embedding(session: SessionDep, dimension_id: int, embedding_model=None) -> bool:
    dimension = session.get(SemanticDimension, dimension_id)
    if dimension is None:
        raise ValueError("SEMANTIC_ASSET_NOT_FOUND")
    if embedding_model is None and not settings.EMBEDDING_ENABLED:
        return False
    try:
        embedding = _get_embedding_model(embedding_model).embed_query(build_dimension_embedding_text(dimension))
        dimension.embedding = embedding
        session.add(dimension)
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        _record_embedding_failure("DIMENSION", dimension_id, exc)
        return False


def _rebuild_metric_embedding_job(metric_id: int) -> None:
    session = session_maker()
    try:
        rebuild_metric_embedding(session, metric_id)
    finally:
        session_maker.remove()


def _rebuild_dimension_embedding_job(dimension_id: int) -> None:
    session = session_maker()
    try:
        rebuild_dimension_embedding(session, dimension_id)
    finally:
        session_maker.remove()


def submit_metric_embedding_rebuild(metric_id: int) -> None:
    executor.submit(_rebuild_metric_embedding_job, metric_id)


def submit_dimension_embedding_rebuild(dimension_id: int) -> None:
    executor.submit(_rebuild_dimension_embedding_job, dimension_id)


def rebuild_missing_approved_embeddings(session: SessionDep, limit: int = 100, embedding_model=None) -> dict[str, int]:
    limit = max(limit, 0)
    result = {"processed": 0, "metrics": 0, "dimensions": 0, "failed": 0}
    if limit == 0:
        return result

    metric_ids = list(
        session.execute(
            select(SemanticMetric.id)
            .where(
                SemanticMetric.status == AssetStatus.APPROVED.value,
                SemanticMetric.embedding.is_(None),
            )
            .order_by(SemanticMetric.updated_at.desc().nullslast(), SemanticMetric.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for metric_id in metric_ids:
        if rebuild_metric_embedding(session, metric_id, embedding_model=embedding_model):
            result["metrics"] += 1
        else:
            result["failed"] += 1
        result["processed"] += 1

    remaining = limit - result["processed"]
    if remaining <= 0:
        return result

    dimension_ids = list(
        session.execute(
            select(SemanticDimension.id)
            .where(
                SemanticDimension.status == AssetStatus.APPROVED.value,
                SemanticDimension.embedding.is_(None),
            )
            .order_by(SemanticDimension.updated_at.desc().nullslast(), SemanticDimension.id.desc())
            .limit(remaining)
        )
        .scalars()
        .all()
    )
    for dimension_id in dimension_ids:
        if rebuild_dimension_embedding(session, dimension_id, embedding_model=embedding_model):
            result["dimensions"] += 1
        else:
            result["failed"] += 1
        result["processed"] += 1
    return result


def _rebuild_missing_approved_embeddings_job(limit: int) -> None:
    session = session_maker()
    try:
        rebuild_missing_approved_embeddings(session, limit=limit)
    except Exception as exc:
        _record_embedding_failure("COMPENSATION", 0, exc)
    finally:
        session_maker.remove()


def submit_missing_approved_embeddings(limit: int = 100) -> None:
    executor.submit(_rebuild_missing_approved_embeddings_job, limit)
