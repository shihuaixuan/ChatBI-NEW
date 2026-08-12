from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.orchestration.graph.capabilities.context import ChatBIRunContext
from apps.chatbi.services.understanding import build_temporal_clarification_options
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.schema_service import DatasetSchemaProvider


@dataclass(frozen=True)
class ClarificationPlan:
    """统一描述一次澄清卡片需要询问什么。"""

    clarification_type: str
    prompt: str
    slots: list[str]
    options: list[dict[str, Any]]
    allowed_update_path: str
    question_key: str
    input_type: str = "single_select_with_text"


class ClarificationCardBuilder:
    """把业务澄清计划转换成前端可渲染的稳定卡片协议。"""

    def build(self, plan: ClarificationPlan) -> dict[str, Any]:
        return {
            "prompt": plan.prompt,
            "options": plan.options,
            "response_schema": self._response_schema(plan),
            "allowed_update_paths": [plan.allowed_update_path],
        }

    @staticmethod
    def _response_schema(plan: ClarificationPlan) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for slot in plan.slots:
            if slot == "domain_id":
                properties[slot] = {"type": "integer"}
            elif slot == "temporal_plan":
                properties[slot] = {"type": "object"}
            elif slot == "dimension_values":
                properties[slot] = {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                }
            else:
                properties[slot] = {"type": "string"}
        properties["skipped"] = {"type": "boolean"}
        dimension_value_fields = _dimension_value_fields_from_options(plan.options)
        return {
            "type": "object",
            "properties": properties,
            "x-card": {
                "card_type": "clarification",
                "clarification_type": plan.clarification_type,
                "input_type": plan.input_type,
                "question_key": plan.question_key,
                **(
                    {"dimension_value_fields": dimension_value_fields}
                    if dimension_value_fields
                    else {}
                ),
            },
        }


def _dimension_value_fields_from_options(options: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for option in options:
        value = option.get("value") if isinstance(option, dict) else {}
        if not isinstance(value, dict):
            continue
        for field in value.get("dimension_value_fields") or []:
            text = str(field or "").strip()
            if text and text not in fields:
                fields.append(text)
    return fields


class InteractionAdapter:
    """ChatBI v1 澄清交互适配器，负责生成用户可回答的结构化交互请求。"""

    def __init__(self, schema_provider: DatasetSchemaProvider | None = None) -> None:
        self._schema_provider = schema_provider
        self._card_builder = ClarificationCardBuilder()

    def ask_rewrite_clarification(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        missing_slots = [str(slot) for slot in ctx.rewrite.get("missing_slots") or []]
        slots = missing_slots or ["metric"]
        return self._card_builder.build(
            ClarificationPlan(
                clarification_type="rewrite_slots",
                prompt=self._rewrite_prompt(slots),
                slots=slots,
                options=self._rewrite_options(slots, ctx),
                allowed_update_path="variables.rewrite_response",
                question_key=f"rewrite:{','.join(slots)}",
            )
        )

    def ask_intent_clarification(self, request: dict[str, Any]) -> dict[str, Any]:
        intent = ChatBIRunContext(request).intent
        conflict_slots = intent.get("conflict_slots") or []
        ambiguous_slots = intent.get("ambiguous_slots") or []
        prompt = "请确认你想进行哪类分析。"
        if conflict_slots:
            prompt = "当前问题里存在互相冲突的分析要求，请选择优先处理的分析方式。"
        elif ambiguous_slots and "metric" in ambiguous_slots:
            prompt = "当前问题的指标不够明确，请确认你想分析的指标或分析方式。"
        return self._card_builder.build(
            ClarificationPlan(
                clarification_type="intent",
                prompt=prompt,
                slots=["intent"],
                options=self._intent_options(),
                allowed_update_path="variables.intent_response",
                question_key=f"intent:{self._intent_question_key(intent)}",
            )
        )

    def ask_metric_selection(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._card_builder.build(
            ClarificationPlan(
                clarification_type="metric_selection",
                prompt="请选择要分析的指标。",
                slots=["metric"],
                options=self._metric_selection_options(request),
                allowed_update_path="variables.metric_selection",
                question_key="metric_selection",
            )
        )

    def ask_cross_model_split(self, request: dict[str, Any]) -> dict[str, Any]:
        """询问用户是否将跨模型问题拆成多个独立查询。"""

        return self._card_builder.build(
            ClarificationPlan(
                clarification_type="cross_model_split",
                prompt="问题包含不同模型的指标，直接关联可能造成重复计算。是否拆分为独立查询？",
                slots=["cross_model_action"],
                options=[
                    {"label": "拆分查询", "value": {"cross_model_action": "split"}},
                    {"label": "取消查询", "value": {"cross_model_action": "cancel"}},
                ],
                allowed_update_path="variables.cross_model_response",
                question_key="cross_model_split",
            )
        )

    def ask_slot_clarification(self, request: dict[str, Any]) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        intent = ctx.intent
        slot_issues = self._slot_issues(intent)
        slot_issue_types = {str(issue.get("slot_type") or "") for issue in slot_issues}
        ambiguous_slots = [str(slot) for slot in intent.get("ambiguous_slots") or []]
        if "time_range" in slot_issue_types:
            raw_temporal_plan = intent.get("temporal_plan")
            temporal_plan: dict[str, Any] = (
                raw_temporal_plan if isinstance(raw_temporal_plan, dict) else {}
            )
            ambiguity_codes = {
                str(item.get("code") or "")
                for item in temporal_plan.get("ambiguities") or []
                if isinstance(item, dict)
            }
            options = build_temporal_clarification_options(ambiguity_codes)
            prompt = "请提供明确的时间范围。"
            if "time_range_conflict" in ambiguity_codes:
                prompt = "问题中存在多个时间范围，请确认本次查询使用哪个时间范围。"
            elif "time_expression_unsupported" in ambiguity_codes:
                prompt = "当前时间表达暂不支持，请提供明确的起止日期。"
            return self._card_builder.build(
                ClarificationPlan(
                    clarification_type="time_range",
                    prompt=prompt,
                    slots=["temporal_confirmation", "temporal_plan"],
                    options=options,
                    allowed_update_path="variables.slot_response",
                    question_key="time_range:" + ",".join(sorted(ambiguity_codes)),
                )
            )
        if "subject_domain" in slot_issue_types:
            return self._card_builder.build(
                ClarificationPlan(
                    clarification_type="subject_domain",
                    prompt="请确认这个问题属于哪个主题域。",
                    slots=["subject_domain", "domain_id"],
                    options=self._subject_domain_options(ctx, intent),
                    allowed_update_path="variables.slot_response",
                    question_key=f"subject_domain:{self._subject_domain_question_key(intent)}",
                )
            )
        if slot_issue_types.intersection({"dimension", "dimension_value"}):
            dimension_names = self._dimension_names(intent, slot_issues)
            dimension_name = dimension_names[0]
            return self._card_builder.build(
                ClarificationPlan(
                    clarification_type="dimension_usage",
                    prompt=f"请确认“{dimension_name}”这个维度的使用方式。",
                    slots=["dimension", "dimension_usage", "dimension_values"],
                    options=[
                        {
                            "label": f"按{dimension_name}分组查看",
                            "value": {
                                "dimension": dimension_name,
                                "dimension_usage": "group_by",
                            },
                        },
                        {
                            "label": f"筛选某个具体{dimension_name}",
                            "value": {
                                "dimension": dimension_name,
                                "dimension_usage": "filter_value_required",
                                "dimension_value_fields": dimension_names,
                            },
                        },
                        {
                            "label": f"不使用{dimension_name}维度",
                            "value": {
                                "dimension": dimension_name,
                                "dimension_usage": "ignore",
                            },
                        },
                    ],
                    allowed_update_path="variables.slot_response",
                    question_key=f"dimension_usage:{dimension_name}",
                    input_type="dimension_value_form",
                )
            )
        return self._card_builder.build(
            ClarificationPlan(
                clarification_type="slot",
                prompt="请补充问题中的关键信息。",
                slots=list(slot_issue_types) or ambiguous_slots or ["value"],
                options=[],
                allowed_update_path="variables.slot_response",
                question_key="slot:"
                + ",".join(list(slot_issue_types) or ambiguous_slots or ["value"]),
            )
        )

    @staticmethod
    def _slot_issues(intent: dict[str, Any]) -> list[dict[str, Any]]:
        raw_validation = intent.get("validation")
        validation: dict[str, Any] = (
            raw_validation if isinstance(raw_validation, dict) else {}
        )
        issues = validation.get("slot_issues")
        return (
            [issue for issue in issues if isinstance(issue, dict)]
            if isinstance(issues, list)
            else []
        )

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
        ctx: ChatBIRunContext,
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        schema = self._load_schema(ctx)
        if "metric" in slots or "analysis_object" in slots:
            options.extend(
                self._asset_options(ctx, "metrics", "metric")
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
                self._asset_options(ctx, "dimensions", "dimension")
                or self._schema_asset_options(schema, "dimensions", "dimension")
                or self._default_dimension_options()
            )
        return options

    def _asset_options(
        self, ctx: ChatBIRunContext, group_name: str, slot_name: str
    ) -> list[dict[str, Any]]:
        candidate_groups = (
            ctx.knowledge.get("candidate_groups")
            if isinstance(ctx.knowledge.get("candidate_groups"), dict)
            else {}
        )
        candidates = (
            candidate_groups.get(group_name)
            if isinstance(candidate_groups.get(group_name), list)
            else []
        )
        return [
            self._asset_option(candidate, slot_name)
            for candidate in candidates[:5]
            if isinstance(candidate, dict)
        ]

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
        schema: DatasetSchema | None,
        group_name: str,
        slot_name: str,
    ) -> list[dict[str, Any]]:
        if schema is None:
            return []
        elements = schema.metrics if group_name == "metrics" else schema.dimensions
        return [
            self._schema_element_option(element, slot_name) for element in elements[:5]
        ]

    def _load_schema(self, ctx: ChatBIRunContext) -> DatasetSchema | None:
        if self._schema_provider is None:
            return None
        if ctx.dataset_id is None:
            return None
        try:
            return self._schema_provider.build_dataset_schema(
                ctx.tenant_id, ctx.dataset_id
            )
        except Exception:
            return None

    @staticmethod
    def _schema_element_option(
        element: SchemaElement, slot_name: str
    ) -> dict[str, Any]:
        label = element.name or element.biz_name
        return {"label": label, "value": {slot_name: label, "asset_id": element.id}}

    def _subject_domain_options(
        self, ctx: ChatBIRunContext, intent: dict[str, Any]
    ) -> list[dict[str, Any]]:
        schema = self._load_schema(ctx)
        domains = getattr(schema, "subject_domains", []) if schema is not None else []
        candidates = [domain for domain in domains if isinstance(domain, dict)]
        candidate_ids = self._subject_domain_candidate_ids(intent)
        if candidate_ids:
            candidates = [
                domain
                for domain in candidates
                if self._int_or_none(domain.get("domain_id")) in candidate_ids
            ]
        if not candidates and candidate_ids:
            return [
                {
                    "label": f"主题域 {domain_id}",
                    "value": {"subject_domain": str(domain_id), "domain_id": domain_id},
                }
                for domain_id in candidate_ids
            ]
        return [
            {
                "label": str(
                    domain.get("name")
                    or domain.get("domain_name")
                    or domain.get("biz_name")
                    or ""
                ),
                "value": {
                    "subject_domain": str(
                        domain.get("name")
                        or domain.get("domain_name")
                        or domain.get("biz_name")
                        or ""
                    ),
                    "domain_id": self._int_or_none(domain.get("domain_id")),
                },
            }
            for domain in candidates
            if self._int_or_none(domain.get("domain_id")) is not None
        ]

    @classmethod
    def _subject_domain_question_key(cls, intent: dict[str, Any]) -> str:
        candidate_ids = cls._subject_domain_candidate_ids(intent)
        if candidate_ids:
            return ",".join(str(domain_id) for domain_id in candidate_ids)
        return "unknown"

    @classmethod
    def _subject_domain_candidate_ids(cls, intent: dict[str, Any]) -> list[int]:
        subject_domain = (
            intent.get("subject_domain")
            if isinstance(intent.get("subject_domain"), dict)
            else {}
        )
        raw_ids = subject_domain.get("candidate_domain_ids")
        if not isinstance(raw_ids, list):
            return []
        result: list[int] = []
        for item in raw_ids:
            domain_id = cls._int_or_none(item)
            if domain_id is not None and domain_id not in result:
                result.append(domain_id)
        return result

    @staticmethod
    def _intent_question_key(intent: dict[str, Any]) -> str:
        conflict_slots = [str(slot) for slot in intent.get("conflict_slots") or []]
        ambiguous_slots = [str(slot) for slot in intent.get("ambiguous_slots") or []]
        if conflict_slots:
            return "conflict:" + ",".join(conflict_slots)
        if ambiguous_slots:
            return ",".join(ambiguous_slots)
        confidence = intent.get("confidence")
        if isinstance(confidence, (int, float)) and float(confidence) < 0.8:
            return "low_confidence"
        return "unknown"

    @staticmethod
    def _dimension_name(intent: dict[str, Any]) -> str:
        return InteractionAdapter._dimension_names(intent, [])[0]

    @staticmethod
    def _dimension_names(
        intent: dict[str, Any], slot_issues: list[dict[str, Any]]
    ) -> list[str]:
        names: list[str] = []
        for issue in slot_issues:
            if str(issue.get("slot_type") or "") not in {
                "dimension",
                "dimension_value",
            }:
                continue
            name = str(issue.get("dimension") or "").strip()
            if name and name not in names:
                names.append(name)
        dimension_slots = (
            intent.get("dimension_slots")
            if isinstance(intent.get("dimension_slots"), list)
            else []
        )
        for slot in dimension_slots:
            if isinstance(slot, dict) and slot.get("name"):
                name = str(slot["name"])
                if name not in names:
                    names.append(name)
        dimension_mentions = (
            intent.get("dimension_mentions")
            if isinstance(intent.get("dimension_mentions"), list)
            else []
        )
        for mention in dimension_mentions:
            name = str(mention)
            if name and name not in names:
                names.append(name)
        return names or ["维度"]

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

    def _metric_selection_options(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        knowledge = ChatBIRunContext(request).knowledge
        for ambiguity in knowledge.get("ambiguities", []) or []:
            if ambiguity.get("type") != "metric":
                continue
            candidates = ambiguity.get("candidates", [])
            if candidates:
                return [self._metric_option(candidate) for candidate in candidates]
        candidate_groups = (
            knowledge.get("candidate_groups")
            if isinstance(knowledge.get("candidate_groups"), dict)
            else {}
        )
        metrics = (
            candidate_groups.get("metrics")
            if isinstance(candidate_groups.get("metrics"), list)
            else []
        )
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
