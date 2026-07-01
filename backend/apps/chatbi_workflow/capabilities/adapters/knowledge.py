from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.capabilities.adapters.time_slots import (
    normalize_time_range_payload,
)
from apps.headless.asset_document import HeadlessAssetDocumentBuilder
from apps.headless.models import HeadlessAssetDocument
from apps.headless.schemas import DataSetSchema, SchemaElement, SchemaElementMatch
from apps.headless.service import HeadlessSchemaBuilder, HeadlessSchemaMapper


class CandidateGate:
    """候选资产决策器，负责把检索候选转换为绑定、歧义或未命中。"""

    def __init__(self, accept_score: float = 0.85, low_confidence_score: float = 0.65, ambiguity_gap: float = 0.12) -> None:
        self.accept_score = accept_score
        self.low_confidence_score = low_confidence_score
        self.ambiguity_gap = ambiguity_gap

    def decide(
        self,
        candidate_groups: dict[str, list[dict[str, Any]]],
        *,
        expected_metric_mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        metrics = sorted(candidate_groups.get("metrics", []), key=lambda item: item.get("score", 0), reverse=True)
        dimensions = sorted(candidate_groups.get("dimensions", []), key=lambda item: item.get("score", 0), reverse=True)
        values = sorted(candidate_groups.get("values", []), key=lambda item: item.get("score", 0), reverse=True)
        terms = sorted(candidate_groups.get("terms", []), key=lambda item: item.get("score", 0), reverse=True)

        explicit_metrics = self._metrics_for_explicit_mentions(metrics, expected_metric_mentions or [])

        if not explicit_metrics and len(metrics) >= 2 and self._is_close(metrics[0], metrics[1]):
            return {
                "status": "metric_ambiguous",
                "selected_assets": {"metrics": [], "dimensions": dimensions, "values": values, "terms": terms},
                "decision": {
                    "status": "ambiguous",
                    "strategy": "candidate_gate",
                    "reason": "Top 指标候选分数接近，无法安全绑定唯一指标。",
                },
                "ambiguities": [{"type": "metric", "candidates": metrics}],
            }

        if metrics and metrics[0].get("score", 0) < self.low_confidence_score:
            return {
                "status": "missed",
                "selected_assets": {"metrics": [], "dimensions": dimensions, "values": values, "terms": terms},
                "decision": {
                    "status": "low_confidence",
                    "strategy": "candidate_gate",
                    "reason": "Top 指标候选低于最低置信阈值。",
                },
                "ambiguities": [],
            }

        return {
            "status": "hit",
            "selected_assets": {
                "metrics": explicit_metrics or metrics[:1],
                "dimensions": dimensions,
                "values": values,
                "terms": terms,
            },
            "decision": {
                "status": "accepted",
                "strategy": "candidate_gate",
                "reason": "候选资产通过确定性阈值检查。",
            },
            "ambiguities": [],
        }

    @staticmethod
    def _metrics_for_explicit_mentions(
        metrics: list[dict[str, Any]],
        mentions: list[str],
    ) -> list[dict[str, Any]]:
        """为每个显式指标提及选择一个最高分候选，并按提及顺序返回。"""

        selected: list[dict[str, Any]] = []
        selected_ids: set[int] = set()
        for mention in mentions:
            normalized_mention = _normalize_text(mention)
            if not normalized_mention:
                continue
            matching: list[dict[str, Any]] = []
            exact_matching: list[dict[str, Any]] = []
            for metric in metrics:
                asset_id = _int_or_none(metric.get("asset_id"))
                if asset_id is None or asset_id in selected_ids:
                    continue
                texts = (
                    metric.get("matched_text"),
                    metric.get("name"),
                    metric.get("biz_name"),
                )
                normalized_texts = [_normalize_text(text) for text in texts]
                if not any(
                    text and (text == normalized_mention or text in normalized_mention or normalized_mention in text)
                    for text in normalized_texts
                ):
                    continue
                matching.append(metric)
                if normalized_mention in normalized_texts:
                    exact_matching.append(metric)
            candidates = exact_matching or matching
            if not candidates:
                continue
            # “人数”一类宽泛词仍交给原有歧义流程，不擅自选择相近候选。
            if len(candidates) >= 2 and float(candidates[0].get("score") or 0) - float(
                candidates[1].get("score") or 0
            ) < 0.12:
                return []
            selected.append(candidates[0])
            selected_ids.add(int(candidates[0]["asset_id"]))
        return selected

    def _is_close(self, top: dict[str, Any], second: dict[str, Any]) -> bool:
        top_score = float(top.get("score") or 0)
        second_score = float(second.get("score") or 0)
        if top_score < self.low_confidence_score or second_score < self.low_confidence_score:
            return False
        return top_score - second_score < self.ambiguity_gap


class HeadlessKnowledgeAdapter:
    """基于 Headless DataSetSchema 的知识检索适配器。"""

    def __init__(
        self,
        schema_builder: HeadlessSchemaBuilder | None = None,
        schema_mapper: HeadlessSchemaMapper | None = None,
        candidate_gate: CandidateGate | None = None,
        document_retriever: HeadlessDocumentRetriever | None = None,
    ) -> None:
        self._schema_builder = schema_builder or HeadlessSchemaBuilder()
        self._schema_mapper = schema_mapper or HeadlessSchemaMapper()
        self._candidate_gate = candidate_gate or CandidateGate()
        self._document_retriever = document_retriever or HeadlessDocumentRetriever()

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        raw_request = request.get("request", {})
        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        rewrite = variables.get("rewrite") if isinstance(variables.get("rewrite"), dict) else {}
        question = str(rewrite.get("rewritten_question") or raw_request.get("question") or "").strip()
        dataset_id = raw_request.get("dataset_id")
        oid = raw_request.get("tenant_id") or raw_request.get("oid") or 1

        if not question or dataset_id is None:
            return self._missed(dataset_id, "missing_question_or_dataset")

        schema = self._schema_builder.build_dataset_schema(int(oid), int(dataset_id))
        intent = variables.get("intent") if isinstance(variables.get("intent"), dict) else {}
        subject_domain = self._selected_subject_domain(schema, intent)
        retrieval_schema = self._schema_scoped_by_subject_domain(schema, subject_domain)
        candidate_groups = self._retrieve_candidate_groups(question, intent, retrieval_schema, int(oid))
        self._rerank_candidate_groups_by_intent(candidate_groups, intent)
        metric_mentions = intent.get("metric_mentions")
        gate_result = self._candidate_gate.decide(
            candidate_groups,
            expected_metric_mentions=metric_mentions if isinstance(metric_mentions, list) else [],
        )
        selected_assets = self._constrain_selected_assets_to_metric_models(gate_result["selected_assets"])
        selected_assets = self._constrain_selected_dimensions_by_intent(selected_assets, intent)
        missing_slots = self._missing_required_slots(intent, selected_assets, gate_result["ambiguities"])
        if missing_slots:
            reason_code = (
                "TIME_RANGE_PROVIDED_BUT_TIME_DIMENSION_MISSING"
                if "time_dimension" in missing_slots and self._time_range_provided(intent)
                else "MISSING_REQUIRED_INTENT_SLOTS"
            )
            return self._missed(
                dataset_id,
                "missing_required_intent_slots",
                schema=retrieval_schema,
                candidate_groups=candidate_groups,
                subject_domain=subject_domain,
                decision={
                    "status": "slot_missing",
                    "strategy": "intent_slot_grounding",
                    "reason_code": reason_code,
                    "reason": "意图要求的槽位未能绑定到 Headless 资产。",
                    "missing_required_slots": missing_slots,
                    "missing_slots": missing_slots,
                },
            )
        if gate_result["status"] == "missed" or (not any(selected_assets.values()) and not gate_result["ambiguities"]):
            return self._missed(
                dataset_id,
                "no_headless_asset_match",
                schema=retrieval_schema,
                candidate_groups=candidate_groups,
                subject_domain=subject_domain,
            )

        return {
            "hit": True,
            "status": gate_result["status"],
            "dataset_id": int(dataset_id),
            "schema_version": self._schema_version(retrieval_schema),
            "index_version": self._index_version(retrieval_schema),
            "tables": self._tables(retrieval_schema, selected_assets),
            "fields": self._fields(selected_assets),
            "metrics": [item["biz_name"] for item in selected_assets["metrics"]],
            "dimensions": [item["biz_name"] for item in selected_assets["dimensions"]],
            "terms": [item["biz_name"] for item in selected_assets["terms"]],
            "examples": [],
            "candidate_groups": candidate_groups,
            "selected_assets": selected_assets,
            "slot_bindings": self._slot_bindings(selected_assets, intent),
            "subject_domain": subject_domain or {},
            "decision": gate_result["decision"],
            "ambiguities": gate_result["ambiguities"],
        }

    @staticmethod
    def _constrain_selected_assets_to_metric_models(
        selected_assets: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        metric_model_ids = {
            item.get("model_id")
            for item in selected_assets.get("metrics", [])
            if item.get("model_id") is not None
        }
        if not metric_model_ids:
            return selected_assets
        constrained: dict[str, list[dict[str, Any]]] = {}
        for group_name, items in selected_assets.items():
            if group_name == "metrics":
                constrained[group_name] = items
                continue
            constrained[group_name] = [
                item
                for item in items
                if item.get("model_id") in metric_model_ids
            ]
        return constrained

    @classmethod
    def _constrain_selected_dimensions_by_intent(
        cls,
        selected_assets: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """只保留查询计划明确需要的维度，候选维度不能直接进入 SQL。"""

        dimensions = selected_assets.get("dimensions", [])
        selected_ids: set[int] = set()
        for slot in intent.get("dimension_slots") or []:
            if not isinstance(slot, dict):
                continue
            if str(slot.get("role") or "").lower() not in {"group_by", "display", "filter"}:
                continue
            candidate = cls._match_dimension_candidate(str(slot.get("name") or ""), dimensions)
            if candidate is not None:
                selected_ids.add(int(candidate["asset_id"]))
        for slot in cls._dimension_filter_slots(intent):
            candidate = cls._match_dimension_candidate(str(slot.get("name") or ""), dimensions)
            if candidate is not None:
                selected_ids.add(int(candidate["asset_id"]))

        query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
        required_slots = set(cls._text_list(intent.get("required_slot_types")))
        time_range = intent.get("time_range") if isinstance(intent.get("time_range"), dict) else {}
        normalized_time = normalize_time_range_payload(time_range).get("normalized") if time_range else None
        supported_time = isinstance(normalized_time, dict) and normalized_time.get("kind") != "unsupported"
        current_snapshot = _normalize_text(time_range.get("raw")) in {"当前", "目前"}
        needs_time = (
            supported_time
            or bool(query_shape.get("time_grain"))
            or (
                not current_snapshot
                and (
                    "time_dimension" in required_slots
                    or bool(cls._text_list(intent.get("time_mentions")))
                )
            )
        )
        if needs_time:
            default_time = cls._default_time_dimension_candidate(dimensions)
            if default_time is not None:
                selected_ids.add(int(default_time["asset_id"]))

        constrained = dict(selected_assets)
        constrained["dimensions"] = [
            dimension
            for dimension in dimensions
            if _int_or_none(dimension.get("asset_id")) in selected_ids
        ]
        return constrained

    def _selected_subject_domain(self, schema: DataSetSchema, intent: dict[str, Any]) -> dict[str, Any] | None:
        subject_domain = intent.get("subject_domain") if isinstance(intent.get("subject_domain"), dict) else {}
        if str(subject_domain.get("status") or "").lower() != "selected":
            return None
        domain_id = _int_or_none(subject_domain.get("domain_id"))
        if domain_id is None:
            return None
        for candidate in schema.subject_domains:
            if _int_or_none(candidate.get("domain_id")) != domain_id:
                continue
            return {
                "status": "selected",
                "domain_id": domain_id,
                "domain_name": subject_domain.get("domain_name") or candidate.get("name"),
                "domain_biz_name": subject_domain.get("domain_biz_name") or candidate.get("biz_name"),
                "confidence": subject_domain.get("confidence", 0),
                "reason": subject_domain.get("reason") or "",
                "candidate_domain_ids": subject_domain.get("candidate_domain_ids") or [domain_id],
                "model_ids": candidate.get("model_ids") or [],
            }
        return None

    @staticmethod
    def _schema_scoped_by_subject_domain(
        schema: DataSetSchema,
        subject_domain: dict[str, Any] | None,
    ) -> DataSetSchema:
        if not subject_domain:
            return schema
        model_ids = {
            model_id
            for model_id in (_int_or_none(value) for value in subject_domain.get("model_ids") or [])
            if model_id is not None
        }
        if not model_ids:
            return schema
        metric_ids: set[int] = set()
        dimension_ids: set[int] = set()
        model_names = {
            str(model.get("biz_name"))
            for model in schema.models
            if _int_or_none(model.get("id")) in model_ids and model.get("biz_name")
        }
        metrics = []
        for metric in schema.metrics:
            if metric.model in model_ids:
                metrics.append(metric)
                metric_ids.add(metric.id)
        dimensions = []
        for dimension in schema.dimensions:
            if dimension.model in model_ids:
                dimensions.append(dimension)
                dimension_ids.add(dimension.id)
        return schema.model_copy(
            update={
                "models": [model for model in schema.models if _int_or_none(model.get("id")) in model_ids],
                "model_relations": [
                    relation
                    for relation in schema.model_relations
                    if relation.left in model_names and relation.right in model_names
                ],
                "metrics": metrics,
                "dimensions": dimensions,
                "dimension_values": [value for value in schema.dimension_values if value.model in model_ids],
                "terms": [
                    term
                    for term in schema.terms
                    if _term_related_to_selected_assets(term, metric_ids, dimension_ids)
                ],
            }
        )

    def _retrieve_candidate_groups(
        self,
        question: str,
        intent: dict[str, Any],
        schema: DataSetSchema,
        oid: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """优先使用意图节点提取的自然语言线索分槽位召回资产。"""

        candidate_groups = self._empty_candidate_groups()
        has_intent_mentions = False

        metric_texts = self._text_list(intent.get("metric_mentions"))
        if metric_texts:
            has_intent_mentions = True
            self._merge_allowed_groups(candidate_groups, self._retrieve_for_texts(metric_texts, schema, oid), {"metrics"})

        dimension_texts = self._text_list(intent.get("dimension_mentions"))
        if dimension_texts:
            has_intent_mentions = True
            self._merge_allowed_groups(
                candidate_groups,
                self._retrieve_for_texts(dimension_texts, schema, oid),
                {"dimensions"},
            )

        filter_texts = self._filter_texts(intent.get("filter_mentions"))
        if filter_texts:
            has_intent_mentions = True
            self._merge_allowed_groups(
                candidate_groups,
                self._retrieve_for_texts(filter_texts, schema, oid),
                {"values", "dimensions"},
            )

        time_texts = self._text_list(intent.get("time_mentions"))
        if time_texts:
            has_intent_mentions = True
            self._merge_allowed_groups(
                candidate_groups,
                self._retrieve_for_texts(time_texts, schema, oid),
                {"dimensions", "values"},
            )
            self._merge_candidate_groups(candidate_groups, {"dimensions": self._time_dimension_candidates(schema)})

        fallback_groups = self._retrieve_for_texts([question], schema, oid)
        if not has_intent_mentions:
            return fallback_groups

        # rewritten_question 只做补漏：当意图要求的槽位仍没有候选时，再从整句补充对应资产。
        required_slots = set(self._text_list(intent.get("required_slot_types")))
        if "metric" in required_slots and not candidate_groups["metrics"]:
            self._merge_allowed_groups(candidate_groups, fallback_groups, {"metrics"})
        if (
            {"dimension", "time_dimension", "comparison_target"} & required_slots
            and not candidate_groups["dimensions"]
        ):
            self._merge_allowed_groups(candidate_groups, fallback_groups, {"dimensions"})
        if "filter" in required_slots and not candidate_groups["values"]:
            self._merge_allowed_groups(candidate_groups, fallback_groups, {"values", "dimensions"})
        return candidate_groups

    def _rerank_candidate_groups_by_intent(
        self,
        candidate_groups: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any],
    ) -> None:
        """按照意图槽位的自然语言 mention 对候选做二次排序。"""

        rerank_specs = [
            ("metrics", self._text_list(intent.get("metric_mentions")), "metric"),
            ("dimensions", self._text_list(intent.get("dimension_mentions")), "dimension"),
            ("values", self._filter_texts(intent.get("filter_mentions")), "filter"),
        ]
        for group_name, mentions, slot_name in rerank_specs:
            if not mentions:
                continue
            candidates = candidate_groups.get(group_name) or []
            for candidate in candidates:
                self._rerank_candidate(candidate, mentions, slot_name)
            candidates.sort(
                key=lambda item: (
                    -float(item.get("score") or 0),
                    str(item.get("asset_type") or ""),
                    int(item.get("asset_id") or 0),
                )
            )

    def _rerank_candidate(self, candidate: dict[str, Any], mentions: list[str], slot_name: str) -> None:
        """用短语命中、关键词覆盖和字段权重修正原始文本重叠分。"""

        base_score = float(candidate.get("score") or 0)
        best_score = 0.0
        best_reason: list[str] = []
        for mention in mentions:
            mention_text = _normalize_text(mention)
            if not mention_text:
                continue
            for field_name, field_text, field_weight in self._candidate_search_fields(candidate):
                score, reason = _score_candidate_field_by_mention(
                    mention_text=mention_text,
                    display_mention=str(mention).strip(),
                    field_name=field_name,
                    field_text=field_text,
                    field_weight=field_weight,
                    base_score=base_score,
                    slot_name=slot_name,
                )
                if score > best_score:
                    best_score = score
                    best_reason = reason

        if best_score <= 0:
            return

        candidate["base_score"] = base_score
        candidate["score"] = round(best_score, 4)
        candidate["rerank_strategy"] = "intent_slot_phrase_rerank"
        candidate["rerank_reason"] = best_reason

    @staticmethod
    def _candidate_search_fields(candidate: dict[str, Any]) -> list[tuple[str, str, float]]:
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        aliases = payload.get("alias") or payload.get("aliases") or []
        fields: list[tuple[str, str, float]] = [
            ("name", str(candidate.get("name") or payload.get("name") or ""), 1.0),
            ("matched_text", str(candidate.get("matched_text") or ""), 0.92),
            ("biz_name", str(candidate.get("biz_name") or payload.get("biz_name") or ""), 0.55),
            ("description", str(payload.get("description") or ""), 0.72),
        ]
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases if isinstance(aliases, list) else []:
            fields.append(("alias", str(alias or ""), 0.98))
        for field in payload.get("fields") or []:
            fields.append(("field", str(field or ""), 0.45))
        return fields

    def _candidate_groups(self, matches: list[SchemaElementMatch]) -> dict[str, list[dict[str, Any]]]:
        groups = {"metrics": [], "dimensions": [], "values": [], "terms": []}
        seen: set[tuple[str, int]] = set()
        for match in matches:
            element = match.element
            key = (element.type, element.id)
            if key in seen:
                continue
            seen.add(key)
            candidate = self._candidate_from_match(match)
            if element.type == "METRIC":
                groups["metrics"].append(candidate)
            elif element.type == "DIMENSION":
                groups["dimensions"].append(candidate)
            elif element.type == "VALUE":
                groups["values"].append(candidate)
            elif element.type == "TERM":
                groups["terms"].append(candidate)
        return groups

    def _retrieve_for_texts(self, texts: list[str], schema: DataSetSchema, oid: int) -> dict[str, list[dict[str, Any]]]:
        groups = self._empty_candidate_groups()
        for text in texts:
            map_info = self._schema_mapper.map_schema(text, schema)
            matches = map_info.data_set_element_matches.get(schema.data_set.id, [])
            self._merge_candidate_groups(groups, self._candidate_groups(matches))
            self._merge_candidate_groups(groups, self._document_retriever.retrieve(text, schema, oid))
        return groups

    @staticmethod
    def _empty_candidate_groups() -> dict[str, list[dict[str, Any]]]:
        return {"metrics": [], "dimensions": [], "values": [], "terms": []}

    def _merge_allowed_groups(
        self,
        target: dict[str, list[dict[str, Any]]],
        source: dict[str, list[dict[str, Any]]],
        allowed_groups: set[str],
    ) -> None:
        self._merge_candidate_groups(
            target,
            {group_name: candidates for group_name, candidates in source.items() if group_name in allowed_groups},
        )

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @classmethod
    def _filter_texts(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                texts.extend(cls._text_list([item.get("name"), item.get("value")]))
            else:
                texts.extend(cls._text_list([item]))
        return cls._text_list(texts)

    def _time_dimension_candidates(self, schema: DataSetSchema) -> list[dict[str, Any]]:
        candidates = [
            self._candidate_from_time_dimension(dimension)
            for dimension in schema.dimensions
            if self._is_time_dimension(dimension)
        ]
        candidates.sort(key=lambda item: (-float(item.get("score") or 0), item.get("asset_id") or 0))
        return candidates

    @staticmethod
    def _candidate_from_time_dimension(dimension: SchemaElement) -> dict[str, Any]:
        score = 0.88 if dimension.ext_info.get("is_default_time") else 0.76
        return {
            "source": "intent_time_dimension",
            "asset_type": "DIMENSION",
            "asset_id": dimension.id,
            "model_id": dimension.model,
            "name": dimension.name,
            "biz_name": dimension.biz_name,
            "score": score,
            "matched_text": "time_mentions",
            "matched_field": "intent.time_mentions",
            "payload": dimension.model_dump(mode="json"),
        }

    @staticmethod
    def _is_time_dimension(dimension: SchemaElement) -> bool:
        ext_info = dimension.ext_info or {}
        dimension_type = str(ext_info.get("dimension_type") or "").lower()
        semantic_type = str(ext_info.get("semantic_type") or "").lower()
        data_type = str(ext_info.get("dimension_data_type") or "").lower()
        return bool(
            ext_info.get("is_default_time")
            or ext_info.get("time_granularities")
            or dimension_type in {"time", "partition_time"}
            or semantic_type == "time"
            or any(token in data_type for token in ("date", "time", "timestamp"))
        )

    @staticmethod
    def _missing_required_slots(
        intent: dict[str, Any],
        selected_assets: dict[str, list[dict[str, Any]]],
        ambiguities: list[dict[str, Any]],
    ) -> list[str]:
        required_slots = set(HeadlessKnowledgeAdapter._text_list(intent.get("required_slot_types")))
        if HeadlessKnowledgeAdapter._time_range_provided(intent):
            required_slots.add("time_dimension")
        time_range = intent.get("time_range") if isinstance(intent.get("time_range"), dict) else {}
        if _normalize_text(time_range.get("raw")) in {"当前", "目前"}:
            required_slots.discard("time_dimension")
        missing: list[str] = []
        has_metric_ambiguity = any(ambiguity.get("type") == "metric" for ambiguity in ambiguities)
        if "metric" in required_slots and not selected_assets.get("metrics") and not has_metric_ambiguity:
            missing.append("metric")
        if "dimension" in required_slots and not selected_assets.get("dimensions"):
            missing.append("dimension")
        if "time_dimension" in required_slots:
            has_time_dimension = any(
                HeadlessKnowledgeAdapter._is_time_payload(item.get("payload") or {})
                for item in selected_assets.get("dimensions", [])
            )
            if not has_time_dimension:
                missing.append("time_dimension")
        return missing

    @staticmethod
    def _time_range_provided(intent: dict[str, Any]) -> bool:
        time_range = intent.get("time_range") if isinstance(intent, dict) else {}
        return isinstance(time_range, dict) and str(time_range.get("value_status") or "").lower() == "provided"

    @staticmethod
    def _is_time_payload(payload: dict[str, Any]) -> bool:
        ext_info = payload.get("ext_info") if isinstance(payload.get("ext_info"), dict) else {}
        dimension_type = str(ext_info.get("dimension_type") or "").lower()
        semantic_type = str(ext_info.get("semantic_type") or "").lower()
        data_type = str(ext_info.get("dimension_data_type") or "").lower()
        return bool(
            ext_info.get("is_default_time")
            or ext_info.get("time_granularities")
            or dimension_type in {"time", "partition_time"}
            or semantic_type == "time"
            or any(token in data_type for token in ("date", "time", "timestamp"))
        )

    @staticmethod
    def _candidate_from_match(match: SchemaElementMatch) -> dict[str, Any]:
        element = match.element
        return {
            "source": "headless_schema_mapper",
            "asset_type": element.type,
            "asset_id": element.id,
            "model_id": element.model,
            "name": element.name,
            "biz_name": element.biz_name,
            "score": min(max(match.similarity, 0.0), 1.0),
            "matched_text": match.detect_word,
            "matched_field": "name_or_alias",
            "payload": element.model_dump(mode="json"),
        }

    @staticmethod
    def _merge_candidate_groups(
        target: dict[str, list[dict[str, Any]]],
        source: dict[str, list[dict[str, Any]]],
    ) -> None:
        for group_name, candidates in source.items():
            existing = {
                (item.get("asset_type"), item.get("asset_id")): item
                for item in target.setdefault(group_name, [])
            }
            for candidate in candidates:
                key = (candidate.get("asset_type"), candidate.get("asset_id"))
                old = existing.get(key)
                if old is None:
                    target[group_name].append(candidate)
                    existing[key] = candidate
                elif float(candidate.get("score") or 0) > float(old.get("score") or 0):
                    old.update(candidate)

    @classmethod
    def _slot_bindings(
        cls,
        selected_assets: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        intent = intent if isinstance(intent, dict) else {}
        return {
            "metrics": [
                {
                    "asset_type": "METRIC",
                    "asset_id": item["asset_id"],
                    "display_name": item["name"],
                    "biz_name": item["biz_name"],
                    "confidence": item["score"],
                    "source": item.get("source") or "headless_schema_mapper",
                }
                for item in selected_assets.get("metrics", [])
            ],
            "dimensions": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": item["asset_id"],
                    "display_name": item["name"],
                    "biz_name": item["biz_name"],
                    "confidence": item["score"],
                    "source": item.get("source") or "headless_schema_mapper",
                }
                for item in selected_assets.get("dimensions", [])
            ],
            "filters": cls._filter_bindings(selected_assets, intent),
        }

    @classmethod
    def _filter_bindings(
        cls,
        selected_assets: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        filters = [
            {
                "asset_type": "VALUE",
                "asset_id": item["asset_id"],
                "display_name": item["name"],
                "biz_name": item["biz_name"],
                "confidence": item["score"],
                "source": item.get("source") or "headless_schema_mapper",
            }
            for item in selected_assets.get("values", [])
        ]
        filters.extend(cls._dimension_filter_bindings(selected_assets, intent))
        time_filter = cls._time_range_filter_binding(selected_assets, intent)
        if time_filter is not None:
            filters.append(time_filter)
        return filters

    @classmethod
    def _dimension_filter_bindings(
        cls,
        selected_assets: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        for slot in cls._dimension_filter_slots(intent):
            if not isinstance(slot, dict):
                continue
            if str(slot.get("role") or "").lower() != "filter":
                continue
            if str(slot.get("value_status") or "").lower() != "provided":
                continue
            value = slot.get("value")
            if value in (None, ""):
                continue
            dimension = cls._match_dimension_candidate(str(slot.get("name") or ""), selected_assets.get("dimensions", []))
            if dimension is None:
                continue
            value = _strip_dimension_prefix_from_value(value, dimension)
            dedupe_key = (int(dimension["asset_id"]), str(slot.get("operator") or "="), str(value))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            bindings.append(
                {
                    "asset_type": "DIMENSION",
                    "asset_id": dimension["asset_id"],
                    "display_name": dimension["name"],
                    "biz_name": dimension["biz_name"],
                    "operator": slot.get("operator") or "=",
                    "value": value,
                    "confidence": dimension["score"],
                    "source": slot.get("source") or "intent_dimension_slot",
                }
            )
        return bindings

    @staticmethod
    def _dimension_filter_slots(intent: dict[str, Any]) -> list[dict[str, Any]]:
        slots: list[dict[str, Any]] = []
        dimension_slots = intent.get("dimension_slots")
        if isinstance(dimension_slots, list):
            slots.extend([slot for slot in dimension_slots if isinstance(slot, dict)])

        filter_mentions = intent.get("filter_mentions")
        if not isinstance(filter_mentions, list):
            return slots
        for item in filter_mentions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("dimension") or item.get("field") or "").strip()
            value = item.get("value")
            if not name or value in (None, ""):
                continue
            normalized_value = str(value).strip() if isinstance(value, str) else value
            if normalized_value in (None, ""):
                continue
            slots.append(
                {
                    "name": name,
                    "role": "filter",
                    "value": normalized_value,
                    "value_status": "provided",
                    "operator": item.get("operator") or "=",
                    "source": "intent_filter_mention",
                }
            )
        return slots

    @classmethod
    def _time_range_filter_binding(
        cls,
        selected_assets: dict[str, list[dict[str, Any]]],
        intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        time_range = intent.get("time_range")
        if not isinstance(time_range, dict):
            return None
        if str(time_range.get("value_status") or "").lower() != "provided":
            return None
        normalized_time_range = normalize_time_range_payload(time_range)
        value = normalized_time_range.get("normalized") if isinstance(normalized_time_range, dict) else None
        if not isinstance(value, dict) or value.get("kind") == "unsupported":
            return None
        dimension = cls._default_time_dimension_candidate(selected_assets.get("dimensions", []))
        if dimension is None:
            return None
        return {
            "asset_type": "DIMENSION",
            "asset_id": dimension["asset_id"],
            "display_name": dimension["name"],
            "biz_name": dimension["biz_name"],
            "operator": "=",
            "value": value,
            "confidence": dimension["score"],
            "source": "intent_time_range",
        }

    @staticmethod
    def _match_dimension_candidate(name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalized_name = _normalize_text(name)
        if not normalized_name:
            return None
        for candidate in candidates:
            payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
            aliases = payload.get("alias") or payload.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            alias_texts = aliases if isinstance(aliases, list) else []
            texts = [
                candidate.get("name"),
                candidate.get("biz_name"),
                candidate.get("matched_text"),
                payload.get("name"),
                payload.get("biz_name"),
                payload.get("bizName"),
                *alias_texts,
            ]
            normalized_texts = [_normalize_text(text) for text in texts]
            if any(text and (normalized_name == text or normalized_name in text or text in normalized_name) for text in normalized_texts):
                return candidate
        return None

    @classmethod
    def _default_time_dimension_candidate(cls, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        time_dimensions = [
            candidate
            for candidate in candidates
            if cls._is_time_payload(candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {})
        ]
        if not time_dimensions:
            return None
        time_dimensions.sort(
            key=lambda item: (
                -int(bool((item.get("payload") or {}).get("ext_info", {}).get("is_default_time"))),
                -float(item.get("score") or 0),
                int(item.get("asset_id") or 0),
            )
        )
        return time_dimensions[0]

    @staticmethod
    def _time_range_filter_value(raw: Any) -> dict[str, str] | None:
        normalized = _normalize_text(raw)
        if normalized in {"今天", "今日"}:
            return {"kind": "relative_date", "value": "today"}
        return None

    @staticmethod
    def _tables(schema: DataSetSchema, selected_assets: dict[str, list[dict[str, Any]]]) -> list[str]:
        selected_model_ids = {
            item.get("model_id")
            for items in selected_assets.values()
            for item in items
            if item.get("model_id") is not None
        }
        tables: list[str] = []
        for model in schema.models:
            if model.get("id") not in selected_model_ids:
                continue
            table = str(model.get("tableQuery") or "").strip()
            if table and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _fields(selected_assets: dict[str, list[dict[str, Any]]]) -> list[str]:
        fields: list[str] = []
        for item in selected_assets.get("metrics", []):
            payload = item.get("payload") or {}
            for field in payload.get("fields") or []:
                if field not in fields:
                    fields.append(field)
        return fields

    @staticmethod
    def _schema_version(schema: DataSetSchema) -> int | None:
        return _int_or_none(schema.data_set.ext_info.get("schema_version"))

    @staticmethod
    def _index_version(schema: DataSetSchema) -> int | None:
        return _int_or_none(schema.data_set.ext_info.get("index_version"))

    @staticmethod
    def _missed(
        dataset_id: int | None,
        reason: str,
        schema: DataSetSchema | None = None,
        candidate_groups: dict[str, list[dict[str, Any]]] | None = None,
        subject_domain: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "hit": False,
            "status": "missed",
            "dataset_id": int(dataset_id) if dataset_id is not None else None,
            "schema_version": HeadlessKnowledgeAdapter._schema_version(schema) if schema is not None else None,
            "index_version": HeadlessKnowledgeAdapter._index_version(schema) if schema is not None else None,
            "tables": [],
            "fields": [],
            "metrics": [],
            "dimensions": [],
            "terms": [],
            "examples": [],
            "candidate_groups": candidate_groups or {"metrics": [], "dimensions": [], "values": [], "terms": []},
            "selected_assets": {"metrics": [], "dimensions": [], "values": [], "terms": []},
            "slot_bindings": {"metrics": [], "dimensions": [], "filters": []},
            "subject_domain": subject_domain or {},
            "decision": decision
            or {"status": "missed", "strategy": "headless_exact_schema_match", "reason": reason},
            "ambiguities": [],
        }


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _term_related_to_selected_assets(term: SchemaElement, metric_ids: set[int], dimension_ids: set[int]) -> bool:
    relations = term.related_schema_elements or []
    if not relations:
        return False
    for relation in relations:
        relation_type = str(relation.get("type") or "").upper()
        relation_id = _int_or_none(relation.get("id"))
        if relation_type == "METRIC" and relation_id in metric_ids:
            return True
        if relation_type == "DIMENSION" and relation_id in dimension_ids:
            return True
    return False


class HeadlessDocumentRetriever:
    """基于 HeadlessAssetDocument 的轻量 top-k 检索。"""

    def __init__(self, document_builder: HeadlessAssetDocumentBuilder | None = None, top_k: int = 20) -> None:
        self._document_builder = document_builder or HeadlessAssetDocumentBuilder()
        self._top_k = top_k

    def retrieve(self, question: str, schema: DataSetSchema, oid: int) -> dict[str, list[dict[str, Any]]]:
        documents = self._document_builder.build_from_schema(schema, oid=oid, index_version=self._index_version(schema))
        scored = [
            (document, score, matched_field, matched_text)
            for document in documents
            for score, matched_field, matched_text in [self._score_document(question, document)]
            if score > 0
        ]
        scored.sort(key=lambda item: (-item[1], item[0].asset_type, item[0].asset_id))
        groups = {"metrics": [], "dimensions": [], "values": [], "terms": []}
        for document, score, matched_field, matched_text in scored[: self._top_k]:
            candidate = self._candidate_from_document(document, score, matched_field, matched_text)
            if document.asset_type == "METRIC":
                groups["metrics"].append(candidate)
            elif document.asset_type == "DIMENSION":
                groups["dimensions"].append(candidate)
            elif document.asset_type == "VALUE":
                groups["values"].append(candidate)
            elif document.asset_type == "TERM":
                groups["terms"].append(candidate)
        return groups

    @staticmethod
    def _score_document(question: str, document: HeadlessAssetDocument) -> tuple[float, str, str]:
        fields = [
            ("title", document.title, 0.95),
            ("alias_text", document.alias_text, 0.9),
            ("business_text", document.business_text, 0.72),
            ("technical_text", document.technical_text, 0.68),
            ("search_text", document.search_text, 0.66),
        ]
        best = (0.0, "", "")
        for field_name, text, score in fields:
            matched_text = _longest_common_text(question, text)
            if matched_text:
                weighted_score = _coverage_weighted_score(score, question, matched_text)
                if weighted_score > best[0]:
                    best = (weighted_score, field_name, matched_text)
        return best

    @staticmethod
    def _candidate_from_document(
        document: HeadlessAssetDocument,
        score: float,
        matched_field: str,
        matched_text: str,
    ) -> dict[str, Any]:
        payload = document.payload or {}
        return {
            "source": "headless_asset_document",
            "asset_type": document.asset_type,
            "asset_id": document.asset_id,
            "model_id": payload.get("model"),
            "name": payload.get("name") or document.title,
            "biz_name": payload.get("biz_name") or payload.get("bizName") or document.doc_key,
            "score": score,
            "matched_text": matched_text,
            "matched_field": matched_field,
            "payload": payload,
        }

    @staticmethod
    def _index_version(schema: DataSetSchema) -> int:
        value = schema.data_set.ext_info.get("index_version")
        return value if isinstance(value, int) else 0


def _longest_common_text(left: str, right: str | None) -> str:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left or not right:
        return ""
    if right in left:
        return right
    if left in right:
        return left
    for length in range(min(len(left), len(right)), 1, -1):
        for start in range(0, len(left) - length + 1):
            text = left[start : start + length]
            if text in right:
                return text
    return ""


def _coverage_weighted_score(base_score: float, question: str, matched_text: str) -> float:
    question_text = "".join(str(question or "").split())
    matched = "".join(str(matched_text or "").split())
    if not question_text or not matched:
        return base_score
    coverage = min(len(matched) / len(question_text), 1.0)
    return round(min(base_score + 0.4 * coverage, 1.0), 4)


def _score_candidate_field_by_mention(
    mention_text: str,
    display_mention: str,
    field_name: str,
    field_text: str,
    field_weight: float,
    base_score: float,
    slot_name: str,
) -> tuple[float, list[str]]:
    field = _normalize_text(field_text)
    if not field:
        return 0.0, []

    reason = [f"命中字段: {field_name}"]
    if mention_text in field:
        score = 1.08 + 0.22 * field_weight
        reason.insert(0, f"完整命中 {slot_name} mention: {display_mention}")
        return min(score, 1.3), reason

    matched_text = _longest_common_text(mention_text, field)
    char_coverage = len(matched_text) / len(mention_text) if matched_text else 0.0
    mention_tokens = _semantic_tokens(mention_text)
    field_tokens = _semantic_tokens(field)
    token_coverage = _token_coverage(mention_tokens, field_tokens)
    coverage = max(char_coverage, token_coverage)
    if coverage <= 0:
        return 0.0, []

    score = base_score * (0.25 + 0.5 * coverage) + field_weight * (0.08 + 0.22 * coverage)
    if token_coverage:
        reason.append(f"关键词覆盖: {', '.join(_covered_tokens(mention_tokens, field_tokens))}")
    if matched_text:
        reason.append(f"最长文本重叠: {matched_text}")
    if coverage < 0.75:
        reason.append("缺少完整短语，按部分命中降权")
    return min(score, 1.2), reason


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def _strip_dimension_prefix_from_value(value: Any, dimension_candidate: dict[str, Any] | None) -> Any:
    if dimension_candidate is None or not isinstance(value, str):
        return value
    raw_value = value.strip()
    for prefix in _dimension_value_prefixes(dimension_candidate):
        if not prefix or not raw_value.startswith(prefix):
            continue
        stripped = raw_value[len(prefix) :].strip()
        if stripped:
            return stripped
    return raw_value


def _dimension_value_prefixes(dimension_candidate: dict[str, Any]) -> list[str]:
    payload = dimension_candidate.get("payload") if isinstance(dimension_candidate.get("payload"), dict) else {}
    aliases = payload.get("alias") or payload.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    alias_texts = aliases if isinstance(aliases, list) else []
    texts = _unique_texts(
        [
            dimension_candidate.get("name"),
            dimension_candidate.get("matched_text"),
            payload.get("name"),
            payload.get("biz_name"),
            payload.get("bizName"),
            *alias_texts,
        ]
    )
    prefixes: list[str] = []
    for text in texts:
        prefixes.extend([text, *_dimension_name_variants(text)])
    return sorted(_unique_texts(prefixes), key=len, reverse=True)


def _dimension_name_variants(name: str) -> list[str]:
    text = str(name or "").strip()
    variants: list[str] = []
    for suffix in ("ID", "id", "编号", "名称", "维度"):
        if text.endswith(suffix) and len(text) > len(suffix):
            variants.append(text[: -len(suffix)].strip())
    return variants


def _semantic_tokens(text: str) -> list[str]:
    """用常见 BI 词根拆分短中文指标，避免只按“人数”这类泛词打平。"""

    normalized = _normalize_text(text)
    if not normalized:
        return []

    known_terms = [
        "访问人数",
        "访问量",
        "访客数",
        "访问",
        "浏览",
        "点击",
        "关注",
        "转化",
        "分享",
        "咨询",
        "成交",
        "订单",
        "销售额",
        "金额",
        "人数",
        "次数",
        "数量",
        "店铺",
        "商品",
        "客户",
        "用户",
    ]
    tokens = [term for term in known_terms if term in normalized]
    if tokens:
        return _unique_texts(tokens)
    if len(normalized) <= 4:
        return [normalized]
    return _unique_texts([normalized[index : index + 2] for index in range(0, len(normalized) - 1)])


def _token_coverage(mention_tokens: list[str], field_tokens: list[str]) -> float:
    if not mention_tokens:
        return 0.0
    covered = _covered_tokens(mention_tokens, field_tokens)
    return len(covered) / len(mention_tokens)


def _covered_tokens(mention_tokens: list[str], field_tokens: list[str]) -> list[str]:
    covered: list[str] = []
    for token in mention_tokens:
        if token in field_tokens or any(token in field_token or field_token in token for field_token in field_tokens):
            covered.append(token)
    return covered


def _unique_texts(texts: list[str]) -> list[str]:
    result: list[str] = []
    for text in texts:
        if text and text not in result:
            result.append(text)
    return result
