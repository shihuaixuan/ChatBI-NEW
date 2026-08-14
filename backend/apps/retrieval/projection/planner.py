"""把统一问题理解结果确定性投影为独立检索槽位。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.retrieval.models.dto import (
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalSubQuery,
)


class RetrievalQueryPlan(BaseModel):
    """一次请求可复现的分槽检索计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    profile: RetrievalProfileName
    subqueries: tuple[RetrievalSubQuery, ...]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_unique_subqueries(self) -> RetrievalQueryPlan:
        subquery_ids = [item.subquery_id for item in self.subqueries]
        if len(subquery_ids) != len(set(subquery_ids)):
            raise ValueError("检索计划中的 subquery_id 不允许重复")
        return self


class SemanticBindingQueryPlanner:
    """只读取已确认意图，不调用模型、不猜测资产 ID。"""

    def plan(self, request: RetrievalRequest) -> RetrievalQueryPlan:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise ValueError("SEMANTIC_BINDING_QUERY_PLANNER_PROFILE_MISMATCH")

        subqueries: list[RetrievalSubQuery] = []
        metric_mentions = _unique_texts(request.intent.metric_mentions)
        for index, mention in enumerate(metric_mentions, start=1):
            subqueries.append(
                self._subquery(
                    request,
                    subquery_id=f"metric:{index}",
                    purpose=RetrievalPurpose.METRIC,
                    text=mention,
                    resource_types=(RetrievalResourceType.METRIC,),
                )
            )

        # 上游已经确定维度名称和维度值；这里检索具体维度资产，重复名称由指标模型关系消歧。
        seen_dimensions: set[tuple[str, str]] = set()
        dimension_index = 0
        for slot in request.intent.dimension_slots:
            name = _clean_text(slot.name)
            identity = (name.casefold(), slot.role)
            if not name or identity in seen_dimensions:
                continue
            seen_dimensions.add(identity)
            dimension_index += 1
            subqueries.append(
                self._subquery(
                    request,
                    subquery_id=f"dimension:{dimension_index}",
                    purpose=RetrievalPurpose.DIMENSION,
                    text=name,
                    resource_types=(RetrievalResourceType.DIMENSION,),
                    role=slot.role,
                )
            )

        for index, term in enumerate(
            _subject_terms(request.intent.subject_domain), start=1
        ):
            subqueries.append(
                self._subquery(
                    request,
                    subquery_id=f"term:{index}",
                    purpose=RetrievalPurpose.TERM,
                    text=term,
                    resource_types=(RetrievalResourceType.TERM,),
                    required=False,
                )
            )

        fingerprint_payload = [item.model_dump(mode="json") for item in subqueries]
        encoded = json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RetrievalQueryPlan(
            request_id=request.request_id,
            profile=RetrievalProfileName.SEMANTIC_BINDING,
            subqueries=tuple(subqueries),
            fingerprint=hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    def _subquery(
        request: RetrievalRequest,
        *,
        subquery_id: str,
        purpose: RetrievalPurpose,
        text: str,
        resource_types: tuple[RetrievalResourceType, ...],
        role: Literal["group_by", "filter", "ambiguous"] | None = None,
        required: bool = True,
        extra_filters: dict[str, Any] | None = None,
    ) -> RetrievalSubQuery:
        filters: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "dataset_ids": sorted(set(request.scope.dataset_ids)),
            "knowledge_base_ids": sorted(set(request.scope.knowledge_base_ids)),
            "source_ids": sorted(set(request.scope.source_ids)),
            "resource_types": [item.value for item in resource_types],
            "status": "active",
        }
        if request.scope.permission_version:
            filters["permission_version"] = request.scope.permission_version
        filters.update(extra_filters or {})
        return RetrievalSubQuery(
            subquery_id=subquery_id,
            purpose=purpose,
            text=text,
            role=role,
            required=required,
            filters=filters,
        )


def _subject_terms(subject_domain: dict[str, Any]) -> list[str]:
    terms = subject_domain.get("terms") or []
    if not isinstance(terms, list):
        return []
    return _unique_texts(terms)


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


__all__ = ["RetrievalQueryPlan", "SemanticBindingQueryPlanner"]
