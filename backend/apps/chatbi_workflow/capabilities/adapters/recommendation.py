from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.schemas.v1 import RecommendationOutput


class RecommendationAdapter:
    """ChatBI v1 推荐问题节点真实能力适配器。"""

    def recommend(self, request: dict[str, Any]) -> dict[str, Any]:
        """基于当前查询上下文生成可继续追问的问题。"""

        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        raw_request = request.get("request", {})
        current_question = str(raw_request.get("question") or "").strip()
        knowledge = variables.get("knowledge") if isinstance(variables.get("knowledge"), dict) else {}
        metric_name = self._first_asset_name(knowledge, "metrics") or "该指标"
        dimension_name = self._first_asset_name(knowledge, "dimensions")
        sql_execution = variables.get("sql_execution") if isinstance(variables.get("sql_execution"), dict) else {}

        questions = [
            f"查看{metric_name}最近 7 天趋势",
            self._dimension_question(metric_name, dimension_name),
            self._comparison_question(metric_name, sql_execution),
        ]
        return RecommendationOutput(
            questions=self._dedupe_questions(questions, current_question),
        ).model_dump(mode="json")

    def _first_asset_name(self, knowledge: dict[str, Any], asset_type: str) -> str | None:
        """优先使用已确认资产的展示名，保证推荐问题面向业务用户。"""

        selected_assets = knowledge.get("selected_assets")
        if isinstance(selected_assets, dict):
            assets = selected_assets.get(asset_type)
            if isinstance(assets, list):
                for asset in assets:
                    name = self._asset_display_name(asset)
                    if name:
                        return name
        fallback_values = knowledge.get(asset_type)
        if isinstance(fallback_values, list):
            for value in fallback_values:
                name = str(value or "").strip()
                if name:
                    return name
        return None

    @staticmethod
    def _asset_display_name(asset: Any) -> str | None:
        if not isinstance(asset, dict):
            text = str(asset or "").strip()
            return text or None
        for key in ("display_name", "name", "biz_name", "asset_id"):
            value = str(asset.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _dimension_question(metric_name: str, dimension_name: str | None) -> str:
        if dimension_name:
            return f"按{dimension_name}对比{metric_name}"
        return f"按日期查看{metric_name}趋势"

    @staticmethod
    def _comparison_question(metric_name: str, sql_execution: dict[str, Any]) -> str:
        if sql_execution.get("status") == "failed":
            return f"换一个口径查看{metric_name}"
        return f"查看{metric_name}较昨日变化"

    @staticmethod
    def _dedupe_questions(questions: list[str], current_question: str) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for question in questions:
            normalized = question.strip()
            if not normalized or normalized == current_question or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[:3]
