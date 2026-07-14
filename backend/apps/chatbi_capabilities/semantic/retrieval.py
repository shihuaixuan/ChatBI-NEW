"""Agentic `search_semantic_assets` 的稳定能力层入口。"""

from __future__ import annotations

from typing import Any

from apps.retrieval.service import (
    RetrievalService,
    build_retrieval_service,
    build_semantic_binding_request,
)


def retrieve_semantic_assets(
    session: Any,
    *,
    oid: int,
    dataset_id: int,
    question: str,
    intent: dict[str, Any] | None = None,
    max_candidates_per_group: int = 5,
    actor_id: int | None = None,
    request_id: str | None = None,
    retrieval_service: RetrievalService | None = None,
) -> dict[str, Any]:
    """检索语义资产，返回语义包。

    intent 为可选的检索线索（LLM 可传 metric_mentions/dimension_mentions/
    filter_mentions/time_mentions 等，形如图链路意图结构的子集），
    没有时按整句问题检索。
    """

    service = retrieval_service or build_retrieval_service(session)
    request = build_semantic_binding_request(
        request_id=request_id,
        tenant_id=oid,
        actor_id=actor_id or 1,
        dataset_id=dataset_id,
        original_question=question,
        rewritten_question=question,
        intent=intent,
    )
    result = service.retrieve(request)
    return _to_semantic_package(result.legacy_payload, max_candidates_per_group)


def _to_semantic_package(raw: dict[str, Any], max_per_group: int) -> dict[str, Any]:
    """把图侧检索输出裁剪为 Agentic 语义包：候选 + 选中资产 + 歧义提示 + 截断统计。"""

    candidate_groups = raw.get("candidate_groups") or {}
    trimmed_groups: dict[str, list[dict[str, Any]]] = {}
    truncated: dict[str, int] = {}
    for group, items in candidate_groups.items():
        items = items if isinstance(items, list) else []
        trimmed_groups[group] = [_public_candidate(item) for item in items[:max_per_group]]
        if len(items) > max_per_group:
            truncated[group] = len(items) - max_per_group

    return {
        "hit": bool(raw.get("hit")),
        "status": raw.get("status"),
        "dataset_id": raw.get("dataset_id"),
        "tables": raw.get("tables") or [],
        "metrics": raw.get("metrics") or [],
        "dimensions": raw.get("dimensions") or [],
        "terms": raw.get("terms") or [],
        "selected_assets": raw.get("selected_assets") or {},
        "candidate_groups": trimmed_groups,
        "ambiguities": raw.get("ambiguities") or [],
        "decision": raw.get("decision") or {},
        "multi_query_plans": raw.get("multi_query_plans") or [],
        "retrieval_strategy_version": raw.get("retrieval_strategy_version"),
        "retrieval_diagnostics": raw.get("retrieval_diagnostics") or {},
        "truncated": truncated,
    }


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("asset_type", "asset_id", "biz_name", "display_name", "score", "source", "model_id", "description")
    return {key: item.get(key) for key in keep if item.get(key) is not None}
