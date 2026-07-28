from typing import Any

from apps.chatbi.models import SemanticRetrievalData
from apps.retrieval.query.service import (
    RetrievalService,
    build_semantic_binding_request,
)


class SemanticRetrievalService:
    """Agent 与 Graph 共用的语义资产检索入口（直连 Retrieval 公开服务）。"""

    def __init__(self, gateway: RetrievalService) -> None:
        self._gateway = gateway

    def retrieve(self, data: SemanticRetrievalData) -> dict[str, Any]:
        request = build_semantic_binding_request(
            request_id=data.request_id,
            tenant_id=data.workspace_id,
            actor_id=data.user_id or 1,
            dataset_id=data.dataset_id,
            original_question=data.original_question,
            rewritten_question=data.rewritten_question,
            intent=data.intent,
        )
        return self._gateway.retrieve(request).payload

    def retrieve_for_agent(
        self,
        data: SemanticRetrievalData,
        *,
        max_candidates_per_group: int = 5,
    ) -> dict[str, Any]:
        return self.project_agent_package(
            self.retrieve(data),
            max_candidates_per_group=max_candidates_per_group,
        )

    @classmethod
    def filter_authorized_tables(
        cls,
        package: dict[str, Any],
        authorized_tables: list[str],
    ) -> dict[str, Any]:
        """过滤带物理表标识的语义资产，避免返回未授权元数据。"""

        allowed = {table.lower() for table in authorized_tables}
        filtered = cls._filter_nested_assets(package, allowed)
        filtered["tables"] = [
            table
            for table in package.get("tables") or []
            if str(table).lower() in allowed
        ]
        return filtered

    @classmethod
    def _filter_nested_assets(
        cls,
        value: Any,
        allowed: set[str],
    ) -> Any:
        """递归过滤候选、槽位和多查询计划中的物理表引用。"""

        if isinstance(value, list):
            return [
                cls._filter_nested_assets(item, allowed)
                for item in value
                if not cls._has_unauthorized_table(item, allowed)
            ]
        if isinstance(value, dict):
            return {
                key: cls._filter_nested_assets(item, allowed)
                for key, item in value.items()
                if not cls._has_unauthorized_table(item, allowed)
            }
        return value

    @staticmethod
    def _has_unauthorized_table(item: Any, allowed: set[str]) -> bool:
        if not isinstance(item, dict):
            return False
        table = next(
            (
                item.get(key)
                for key in ("table", "table_name", "physical_table")
                if item.get(key)
            ),
            None,
        )
        return table is not None and str(table).lower() not in allowed

    @classmethod
    def project_agent_package(
        cls,
        raw: dict[str, Any],
        *,
        max_candidates_per_group: int,
    ) -> dict[str, Any]:
        """把完整检索结果裁剪为 Agent 上下文使用的语义包。"""

        candidate_groups = raw.get("candidate_groups") or {}
        trimmed_groups: dict[str, list[dict[str, Any]]] = {}
        truncated: dict[str, int] = {}
        for group, items in candidate_groups.items():
            values = items if isinstance(items, list) else []
            trimmed_groups[group] = [
                cls._public_candidate(item)
                for item in values[:max_candidates_per_group]
                if isinstance(item, dict)
            ]
            if len(values) > max_candidates_per_group:
                truncated[group] = len(values) - max_candidates_per_group

        return {
            "hit": bool(raw.get("hit")),
            "status": cls.agent_semantic_status(raw),
            "dataset_id": raw.get("dataset_id"),
            "tables": raw.get("tables") or [],
            "metrics": raw.get("metrics") or [],
            "dimensions": raw.get("dimensions") or [],
            "terms": raw.get("terms") or [],
            "selected_assets": raw.get("selected_assets") or {},
            "slot_bindings": raw.get("slot_bindings") or {},
            "candidate_groups": trimmed_groups,
            "ambiguities": raw.get("ambiguities") or [],
            "decision": raw.get("decision") or {},
            "multi_query_plans": raw.get("multi_query_plans") or [],
            "retrieval_strategy_version": raw.get(
                "retrieval_strategy_version"
            ),
            "retrieval_diagnostics": raw.get("retrieval_diagnostics") or {},
            "truncated": truncated,
        }

    @staticmethod
    def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "asset_type",
            "asset_id",
            "biz_name",
            "display_name",
            "score",
            "source",
            "model_id",
            "description",
            # 物理表标识用于授权过滤，属于可公开的最小必要元数据。
            "table",
            "table_name",
            "physical_table",
        )
        return {key: item.get(key) for key in keys if item.get(key) is not None}

    @staticmethod
    def agent_semantic_status(raw: dict[str, Any]) -> str | None:
        """根据实际歧义槽位生成 Agent 可判断的状态。"""

        decision = raw.get("decision")
        if not isinstance(decision, dict):
            return raw.get("status")
        reason_codes = {
            str(code) for code in decision.get("reason_codes") or [] if code
        }
        if "TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL" in reason_codes:
            return "time_dimension_not_configured"
        if decision.get("status") != "ambiguous":
            return raw.get("status")
        ambiguity_types = {
            str(item.get("type") or "")
            for item in raw.get("ambiguities") or []
            if isinstance(item, dict) and item.get("type")
        }
        if ambiguity_types == {"metric"}:
            return "metric_ambiguous"
        if ambiguity_types == {"dimension"}:
            return "dimension_ambiguous"
        return "semantic_ambiguous"


__all__ = ["SemanticRetrievalService"]
