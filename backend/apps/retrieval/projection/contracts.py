"""不同知识源写入统一索引前共用的严格投影契约。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.retrieval.models.dto import RetrievalResourceType, RetrievalSourceType


class _ProjectionModel(BaseModel):
    """投影边界使用严格且不可变的 DTO。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectedUnit(_ProjectionModel):
    """可以独立建立词法索引和向量的最小检索单元。"""

    unit_key: str = Field(min_length=1, max_length=128)
    content_kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    contextual_text: str = ""
    language: str | None = "zh"
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def embedding_text(self) -> str:
        """返回唯一允许送入 embedding provider 的文本。"""

        return unit_embedding_text(
            content_kind=self.content_kind,
            title=self.title,
            content=self.content,
            contextual_text=self.contextual_text,
        )

    @classmethod
    def create(
        cls,
        *,
        unit_key: str,
        content_kind: str,
        title: str,
        content: str,
        contextual_text: str = "",
        language: str | None = "zh",
        metadata: dict[str, Any] | None = None,
    ) -> ProjectedUnit:
        unit_metadata = metadata or {}
        embedding_text = unit_embedding_text(
            content_kind=content_kind,
            title=title,
            content=content,
            contextual_text=contextual_text,
        )
        return cls(
            unit_key=unit_key,
            content_kind=content_kind,
            title=title,
            content=content,
            contextual_text=contextual_text,
            language=language,
            metadata=unit_metadata,
            content_hash=projection_content_hash(
                {
                    "unit_key": unit_key,
                    "content_kind": content_kind,
                    "title": title,
                    "content": content,
                    "contextual_text": contextual_text,
                    "language": language,
                    "metadata": unit_metadata,
                }
            ),
            embedding_text_hash=hashlib.sha256(embedding_text.encode("utf-8")).hexdigest(),
        )


def unit_embedding_text(
    *,
    content_kind: str,
    title: str,
    content: str,
    contextual_text: str,
) -> str:
    """名称类检索单元只向量化名称值，避免固定标签和数据集上下文稀释语义。"""

    if content_kind in {"name", "aliases", "biz_name"}:
        return content
    return "\n".join(part for part in [title, content, contextual_text] if part)


class ProjectedResource(_ProjectionModel):
    """来源适配器输出的稳定资源及其完整检索单元集合。"""

    tenant_id: int = Field(gt=0)
    namespace: str = Field(min_length=1, max_length=128)
    resource_type: RetrievalResourceType
    source_type: RetrievalSourceType
    source_resource_id: str = Field(min_length=1, max_length=256)
    dataset_id: int | None = Field(default=None, gt=0)
    knowledge_base_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    acl: dict[str, Any] = Field(default_factory=dict)
    visibility: Literal["private", "tenant", "public"] = "tenant"
    permission_version: str | None = None
    source_version: str = Field(min_length=1, max_length=128)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: tuple[ProjectedUnit, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_projection(self) -> ProjectedResource:
        if self.dataset_id is not None and self.knowledge_base_id is not None:
            raise ValueError("检索资源不能同时属于数据集和知识库")
        unit_keys = [unit.unit_key for unit in self.units]
        if len(unit_keys) != len(set(unit_keys)):
            raise ValueError("同一个检索资源内的 unit_key 不允许重复")
        return self


class ProjectedResourceDelta(_ProjectionModel):
    """Indexer 写入新 generation 时需要处理的最小变化集合。"""

    source_resource_id: str = Field(min_length=1)
    resource_changed: bool
    upsert_unit_keys: tuple[str, ...] = ()
    reembed_unit_keys: tuple[str, ...] = ()
    delete_unit_keys: tuple[str, ...] = ()
    unchanged_unit_keys: tuple[str, ...] = ()

    @classmethod
    def between(
        cls,
        previous: ProjectedResource | None,
        current: ProjectedResource,
    ) -> ProjectedResourceDelta:
        if previous is not None and (
            previous.source_resource_id != current.source_resource_id
            or previous.resource_type != current.resource_type
            or previous.source_type != current.source_type
            or previous.tenant_id != current.tenant_id
            or previous.dataset_id != current.dataset_id
            or previous.knowledge_base_id != current.knowledge_base_id
        ):
            raise ValueError("投影差异比较必须针对同一个来源资源")

        previous_units = {unit.unit_key: unit for unit in previous.units} if previous else {}
        current_units = {unit.unit_key: unit for unit in current.units}
        upserts = sorted(
            key
            for key, unit in current_units.items()
            if key not in previous_units or previous_units[key].content_hash != unit.content_hash
        )
        deleted = sorted(set(previous_units) - set(current_units))
        reembeds = sorted(
            key
            for key, unit in current_units.items()
            if key not in previous_units
            or previous_units[key].embedding_text_hash != unit.embedding_text_hash
        )
        unchanged = sorted(
            key
            for key, unit in current_units.items()
            if key in previous_units and previous_units[key].content_hash == unit.content_hash
        )
        resource_changed = previous is None or any(
            [
                previous.content_hash != current.content_hash,
                previous.source_version != current.source_version,
                previous.namespace != current.namespace,
            ]
        )
        return cls(
            source_resource_id=current.source_resource_id,
            resource_changed=resource_changed,
            upsert_unit_keys=tuple(upserts),
            reembed_unit_keys=tuple(reembeds),
            delete_unit_keys=tuple(deleted),
            unchanged_unit_keys=tuple(unchanged),
        )


def projection_content_hash(payload: dict[str, Any]) -> str:
    """以稳定 JSON 编码生成投影内容 hash。"""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ProjectedResource",
    "ProjectedResourceDelta",
    "ProjectedUnit",
    "projection_content_hash",
    "unit_embedding_text",
]
