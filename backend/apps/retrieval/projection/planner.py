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
    """只读取指标和维度短语，不调用模型、不猜测资产 ID。"""

    def plan(self, request: RetrievalRequest) -> RetrievalQueryPlan:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise ValueError("SEMANTIC_BINDING_QUERY_PLANNER_PROFILE_MISMATCH")

        subqueries: list[RetrievalSubQuery] = []
        for index, phrase in enumerate(request.metric_phrases, start=1):
            subqueries.append(
                self._subquery(
                    request,
                    subquery_id=f"metric:{index}",
                    purpose=RetrievalPurpose.METRIC,
                    text=phrase,
                    resource_types=(RetrievalResourceType.METRIC,),
                )
            )

        # 二期只检索问题重写模型输出的维度短语。
        seen_dimensions: set[str] = set()
        dimension_index = 0
        for phrase in request.dimension_phrases:
            name = _clean_text(phrase)
            identity = name.casefold()
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
        role: Literal["group_by", "filter", "display", "ambiguous"] | None = None,
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



def value_lookup_slots(intent: dict[str, Any]) -> list[tuple[str, str]]:
    """推导需要维值归一的 (维度名, 原始筛选值) 列表。

    只检索业务语义值（中文/字母短语）；纯数字或标识符样式的值
    不在维值字典治理范围内，直接透传保留原值。
    子查询编号与该列表顺序一一对应（value:N → 第 N 项），
    payload 侧依赖同一函数还原维度归属。
    """

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for slot in intent.get("dimension_slots") or []:
        if not isinstance(slot, dict):
            continue
        if str(slot.get("role") or "").lower() != "filter":
            continue
        if str(slot.get("value_status") or "").lower() != "provided":
            continue
        dimension_name = _clean_text(slot.get("name"))
        values = slot.get("value")
        for value in values if isinstance(values, list) else [values]:
            text = _clean_text(value)
            key = (dimension_name.casefold(), text.casefold())
            if not dimension_name or not text or key in seen:
                continue
            if not _is_lookup_worthy_value(text):
                continue
            seen.add(key)
            result.append((dimension_name, text))
    return result


def _is_lookup_worthy_value(text: str) -> bool:
    """标识符样式的值（长数字/字母数字混合 ID）不进入维值检索。"""

    if not any(character.isalpha() or "\u4e00" <= character <= "\u9fff" for character in text):
        return False
    digit_ratio = sum(1 for character in text if character.isdigit()) / max(
        len(text), 1
    )
    if len(text) >= 8 and digit_ratio >= 0.3:
        return False
    return True


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


__all__ = [
    "RetrievalQueryPlan",
    "SemanticBindingQueryPlanner",
]
