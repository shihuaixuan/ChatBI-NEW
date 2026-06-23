from __future__ import annotations

from typing import Any, Protocol

from apps.headless.schemas import DataSetSchema, SchemaElement


class InteractionSchemaBuilder(Protocol):
    """交互节点使用的轻量 schema 构建协议。"""

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema: ...


class InteractionAdapter:
    """ChatBI v1 澄清交互适配器，负责生成用户可回答的结构化交互请求。"""

    def __init__(self, schema_builder: InteractionSchemaBuilder | None = None) -> None:
        self._schema_builder = schema_builder

    def ask_rewrite_clarification(self, request: dict[str, Any]) -> dict[str, Any]:
        variables = request.get("variables", {})
        rewrite = variables.get("rewrite") if isinstance(variables, dict) and isinstance(variables.get("rewrite"), dict) else {}
        missing_slots = [str(slot) for slot in rewrite.get("missing_slots") or []]
        slots = missing_slots or ["metric"]
        return {
            "prompt": self._rewrite_prompt(slots),
            "options": self._rewrite_options(slots, request, variables),
            "response_schema": self._response_schema(slots),
            "allowed_update_paths": ["variables.rewrite_response"],
        }

    def ask_intent_clarification(self, request: dict[str, Any]) -> dict[str, Any]:
        variables = request.get("variables", {})
        intent = variables.get("intent") if isinstance(variables, dict) and isinstance(variables.get("intent"), dict) else {}
        conflict_slots = intent.get("conflict_slots") or []
        ambiguous_slots = intent.get("ambiguous_slots") or []
        prompt = "请确认你想进行哪类分析。"
        if conflict_slots:
            prompt = "当前问题里存在互相冲突的分析要求，请选择优先处理的分析方式。"
        elif ambiguous_slots and "metric" in ambiguous_slots:
            prompt = "当前问题的指标不够明确，请确认你想分析的指标或分析方式。"
        return {
            "prompt": prompt,
            "options": self._intent_options(),
            "response_schema": self._response_schema(["intent"]),
            "allowed_update_paths": ["variables.intent_response"],
        }

    def ask_metric_selection(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "prompt": "请选择要分析的指标。",
            "options": self._metric_selection_options(request),
            "response_schema": self._response_schema(["metric"]),
            "allowed_update_paths": ["variables.metric_selection"],
        }

    def _rewrite_prompt(self, slots: list[str]) -> str:
        labels = [self._slot_label(slot) for slot in slots]
        if not labels:
            return "请补充问题中的关键信息。"
        if len(labels) == 1:
            return f"请补充要分析的{labels[0]}。"
        return "请补充要分析的" + "和".join(labels) + "。"

    def _rewrite_options(
        self,
        slots: list[str],
        request: dict[str, Any],
        variables: dict[str, Any],
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        schema = self._load_schema(request)
        if "metric" in slots or "analysis_object" in slots:
            options.extend(
                self._asset_options(variables, "metrics", "metric")
                or self._schema_asset_options(schema, "metrics", "metric")
                or self._default_metric_options()
            )
        if "time_range" in slots:
            options.extend(
                [
                    {"label": "今天", "value": {"time_range": "今天"}},
                    {"label": "最近 7 天", "value": {"time_range": "最近 7 天"}},
                    {"label": "本月", "value": {"time_range": "本月"}},
                ]
            )
        if "dimension" in slots:
            options.extend(
                self._asset_options(variables, "dimensions", "dimension")
                or self._schema_asset_options(schema, "dimensions", "dimension")
                or self._default_dimension_options()
            )
        return options

    def _asset_options(self, variables: dict[str, Any], group_name: str, slot_name: str) -> list[dict[str, Any]]:
        knowledge = variables.get("knowledge") if isinstance(variables.get("knowledge"), dict) else {}
        candidate_groups = (
            knowledge.get("candidate_groups") if isinstance(knowledge.get("candidate_groups"), dict) else {}
        )
        candidates = candidate_groups.get(group_name) if isinstance(candidate_groups.get(group_name), list) else []
        return [self._asset_option(candidate, slot_name) for candidate in candidates[:5] if isinstance(candidate, dict)]

    @staticmethod
    def _asset_option(candidate: dict[str, Any], slot_name: str) -> dict[str, Any]:
        label = (
            candidate.get("display_name")
            or candidate.get("name")
            or candidate.get("biz_name")
            or candidate.get("title")
            or str(candidate.get("asset_id") or "")
        )
        value: dict[str, Any] = {slot_name: str(label)}
        if candidate.get("asset_id") is not None:
            value["asset_id"] = candidate.get("asset_id")
        return {"label": str(label), "value": value}

    def _schema_asset_options(
        self,
        schema: DataSetSchema | None,
        group_name: str,
        slot_name: str,
    ) -> list[dict[str, Any]]:
        if schema is None:
            return []
        elements = schema.metrics if group_name == "metrics" else schema.dimensions
        return [self._schema_element_option(element, slot_name) for element in elements[:5]]

    def _load_schema(self, request: dict[str, Any]) -> DataSetSchema | None:
        if self._schema_builder is None:
            return None
        raw_request = request.get("request", {})
        dataset_id = self._int_or_none(raw_request.get("dataset_id"))
        oid = self._int_or_none(raw_request.get("tenant_id") or raw_request.get("oid")) or 1
        if dataset_id is None:
            return None
        try:
            return self._schema_builder.build_dataset_schema(oid, dataset_id)
        except Exception:
            return None

    @staticmethod
    def _schema_element_option(element: SchemaElement, slot_name: str) -> dict[str, Any]:
        label = element.name or element.biz_name
        return {"label": label, "value": {slot_name: label, "asset_id": element.id}}

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _default_metric_options() -> list[dict[str, Any]]:
        return [
            {"label": "访问人数", "value": {"metric": "访问人数"}},
            {"label": "销售额", "value": {"metric": "销售额"}},
            {"label": "订单数", "value": {"metric": "订单数"}},
        ]

    @staticmethod
    def _default_dimension_options() -> list[dict[str, Any]]:
        return [
            {"label": "按日期", "value": {"dimension": "日期"}},
            {"label": "按店铺", "value": {"dimension": "店铺"}},
            {"label": "按商品", "value": {"dimension": "商品"}},
        ]

    @staticmethod
    def _intent_options() -> list[dict[str, Any]]:
        return [
            {"label": "查指标数值", "value": {"intent": "metric_query"}},
            {"label": "看趋势", "value": {"intent": "trend_analysis"}},
            {"label": "看排名", "value": {"intent": "ranking_analysis"}},
            {"label": "看对比", "value": {"intent": "comparison_analysis"}},
            {"label": "看明细", "value": {"intent": "detail_query"}},
        ]

    def _metric_selection_options(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        variables = request.get("variables", {})
        knowledge = variables.get("knowledge") if isinstance(variables, dict) and isinstance(variables.get("knowledge"), dict) else {}
        for ambiguity in knowledge.get("ambiguities", []) or []:
            if ambiguity.get("type") != "metric":
                continue
            candidates = ambiguity.get("candidates", [])
            if candidates:
                return [self._metric_option(candidate) for candidate in candidates]
        candidate_groups = (
            knowledge.get("candidate_groups") if isinstance(knowledge.get("candidate_groups"), dict) else {}
        )
        metrics = candidate_groups.get("metrics") if isinstance(candidate_groups.get("metrics"), list) else []
        if metrics:
            return [self._metric_option(candidate) for candidate in metrics[:5]]
        return []

    @staticmethod
    def _metric_option(candidate: Any) -> dict[str, Any]:
        if isinstance(candidate, dict):
            label = (
                candidate.get("display_name")
                or candidate.get("name")
                or candidate.get("biz_name")
                or candidate.get("title")
                or str(candidate.get("asset_id") or "")
            )
            value = candidate.get("asset_id") or candidate.get("biz_name") or label
            return {"label": str(label), "value": value}
        text = str(candidate)
        return {"label": text, "value": text}

    @staticmethod
    def _response_schema(slots: list[str]) -> dict[str, Any]:
        properties = {slot: {"type": "string"} for slot in slots}
        properties["skipped"] = {"type": "boolean"}
        return {"type": "object", "properties": properties}

    @staticmethod
    def _slot_label(slot: str) -> str:
        return {
            "metric": "指标",
            "analysis_object": "分析对象",
            "time_range": "时间范围",
            "dimension": "维度",
            "filter": "筛选条件",
            "intent": "分析方式",
        }.get(slot, slot)
