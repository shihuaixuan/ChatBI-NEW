"""旧语义检索函数兼容入口，实际检索统一转发到 ChatBI Service。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models import SemanticRetrievalData
from apps.chatbi.services import SemanticRetrievalService
from apps.retrieval.service import (
    RetrievalService,
    build_retrieval_service,
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

    service = SemanticRetrievalService(
        retrieval_service or build_retrieval_service(session)
    )
    return service.retrieve_for_agent(
        SemanticRetrievalData(
            workspace_id=oid,
            user_id=actor_id,
            dataset_id=dataset_id,
            original_question=question,
            rewritten_question=question,
            intent=intent or {},
            request_id=request_id,
        ),
        max_candidates_per_group=max_candidates_per_group,
    )


def _to_semantic_package(raw: dict[str, Any], max_per_group: int) -> dict[str, Any]:
    """把图侧检索输出裁剪为 Agentic 语义包：候选 + 选中资产 + 歧义提示 + 截断统计。"""

    return SemanticRetrievalService.project_agent_package(
        raw,
        max_candidates_per_group=max_per_group,
    )


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return SemanticRetrievalService._public_candidate(item)


def _agent_semantic_status(raw: dict[str, Any]) -> str | None:
    """Agent 需要知道具体歧义槽位，不能把维度歧义统一描述成指标歧义。"""

    return SemanticRetrievalService.agent_semantic_status(raw)
