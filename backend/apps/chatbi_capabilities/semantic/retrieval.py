"""语义资产检索（Agentic `search_semantic_assets` 工具的领域实现）。

当前形态：翻译垫片——组装最小图节点上下文，只读复用
`chatbi_workflow.capabilities.adapters.knowledge.HeadlessKnowledgeAdapter`，
不改图侧任何代码。

长期契约：`retrieve_semantic_assets` 的签名与返回结构（语义包）不变；
解耦分析 Step 4（检索核心下沉本包）完成后，仅替换内部实现，调用方无感。

依赖说明：本模块是能力层内**唯一**允许 import chatbi_workflow 的例外（垫片期），
Step 4 完成后该 import 必须移除。
"""

from __future__ import annotations

from typing import Any


def retrieve_semantic_assets(
    session,
    *,
    oid: int,
    dataset_id: int,
    question: str,
    intent: dict[str, Any] | None = None,
    max_candidates_per_group: int = 5,
) -> dict[str, Any]:
    """检索语义资产，返回语义包。

    intent 为可选的检索线索（LLM 可传 metric_mentions/dimension_mentions/
    filter_mentions/time_mentions 等，形如图链路意图结构的子集），
    没有时按整句问题检索。
    """

    # 垫片期例外 import（见模块 docstring），延迟导入避免包加载期依赖。
    from apps.chatbi_workflow.capabilities.adapters.knowledge import HeadlessKnowledgeAdapter
    from apps.headless.service import HeadlessSchemaBuilder

    adapter = HeadlessKnowledgeAdapter(schema_builder=HeadlessSchemaBuilder(session))
    raw = adapter.retrieve(
        {
            "request": {
                "question": question,
                "dataset_id": dataset_id,
                "tenant_id": oid,
            },
            "variables": {"intent": intent or {}},
        }
    )
    return _to_semantic_package(raw, max_candidates_per_group)


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
        "truncated": truncated,
    }


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("asset_type", "asset_id", "biz_name", "display_name", "score", "source", "model_id", "description")
    return {key: item.get(key) for key in keep if item.get(key) is not None}
