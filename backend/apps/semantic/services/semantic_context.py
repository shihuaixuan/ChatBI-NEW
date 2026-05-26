from apps.semantic.models.semantic_model import AssetType
from apps.semantic.models.semantic_schema import (
    SemanticAssetMatch,
    SemanticRetrieveResponse,
)

MIN_CONTEXT_SCORE = 0.55
MAX_DESCRIPTION_LENGTH = 120
MAX_CONTEXT_LENGTH = 3000


def _truncate(text: str | None, limit: int = MAX_DESCRIPTION_LENGTH) -> str:
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _join_values(values) -> str:
    return ", ".join(str(value) for value in (values or []) if value)


def _metric_line(match: SemanticAssetMatch) -> str:
    snapshot = match.snapshot or {}
    parts = [
        f"- {match.display_name} ({match.name})",
        f"默认聚合: {snapshot.get('default_agg') or 'SUM'}",
        f"表达式: {snapshot.get('expr') or ''}",
    ]
    aliases = _join_values(snapshot.get("aliases"))
    if aliases:
        parts.append(f"别名: {aliases}")
    description = _truncate(snapshot.get("description"))
    if description:
        parts.append(f"口径: {description}")
    return "；".join(parts)


def _dimension_line(match: SemanticAssetMatch) -> str:
    snapshot = match.snapshot or {}
    parts = [
        f"- {match.display_name} ({match.name})",
        f"维度类型: {snapshot.get('dimension_type') or 'CATEGORY'}",
        f"语义类型: {snapshot.get('semantic_type') or 'UNKNOWN'}",
        f"表达式: {snapshot.get('expr') or ''}",
    ]
    granularities = _join_values(snapshot.get("time_granularities"))
    if granularities:
        parts.append(f"时间粒度: {granularities}")
    aliases = _join_values(snapshot.get("aliases"))
    if aliases:
        parts.append(f"别名: {aliases}")
    description = _truncate(snapshot.get("description"))
    if description:
        parts.append(f"口径: {description}")
    return "；".join(parts)


def _trim_to_limit(context: str, limit: int) -> str:
    if len(context) <= limit:
        return context
    return context[: limit - 3].rstrip() + "..."


def build_prompt_context(matches: SemanticRetrieveResponse | list[SemanticAssetMatch]) -> str:
    if isinstance(matches, SemanticRetrieveResponse):
        all_matches = [*matches.metrics, *matches.dimensions]
    else:
        all_matches = list(matches)

    filtered = [match for match in all_matches if match.score >= MIN_CONTEXT_SCORE]
    metrics = [match for match in filtered if match.asset_type == AssetType.METRIC.value]
    dimensions = [match for match in filtered if match.asset_type == AssetType.DIMENSION.value]
    if not metrics and not dimensions:
        return ""

    sections: list[str] = []
    if metrics:
        sections.append("已审核业务指标:\n" + "\n".join(_metric_line(match) for match in metrics))
    if dimensions:
        sections.append("已审核业务维度:\n" + "\n".join(_dimension_line(match) for match in dimensions))
    return _trim_to_limit("\n\n".join(sections), MAX_CONTEXT_LENGTH)
