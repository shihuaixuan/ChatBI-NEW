"""Knowledge SQL 示例公开快照到统一检索资源的来源投影。"""

from __future__ import annotations

from typing import Any

from apps.knowledge.models.dto import SQLExampleSnapshot, SQLExampleSourceSnapshot
from apps.retrieval.models.dto import RetrievalResourceType, RetrievalSourceType
from apps.retrieval.projection.contracts import (
    ProjectedResource,
    ProjectedUnit,
    projection_content_hash,
)


class SQLExampleSourceProjector:
    """只消费 Knowledge 公开 DTO，不依赖 SQL 示例 ORM 或仓储。"""

    def project(
        self,
        snapshot: SQLExampleSourceSnapshot,
        *,
        namespace: str,
        acl: dict[str, Any] | None = None,
    ) -> list[ProjectedResource]:
        if not namespace.strip():
            raise ValueError("SQL_EXAMPLE_NAMESPACE_REQUIRED")
        return [
            self._project_example(
                snapshot.workspace_id,
                snapshot.source_version,
                namespace,
                example,
                acl or {},
            )
            for example in snapshot.examples
        ]

    @staticmethod
    def _project_example(
        workspace_id: int,
        source_version: str,
        namespace: str,
        example: SQLExampleSnapshot,
        acl: dict[str, Any],
    ) -> ProjectedResource:
        metadata: dict[str, Any] = {
            "example_type": example.example_type,
            "datasource_id": example.datasource_id,
            "assistant_id": example.assistant_id,
            "dataset_id": example.dataset_id,
            # P0-5：认证状态与语义计划指纹随索引下发，供命中优先级与口径卡片使用。
            "verification_status": example.verification_status.value,
            "semantic_plan_summary": _semantic_plan_summary(example.semantic_plan),
            "plan_fingerprint": example.plan_fingerprint,
            "linked_assets": [
                asset.model_dump(mode="json") for asset in example.linked_assets
            ],
        }
        contextual_text = (
            f"参考 SQL：\n{example.sql}"
            if example.sql and example.sql.strip() != example.description.strip()
            else ""
        )
        unit = ProjectedUnit.create(
            unit_key="sql-example",
            content_kind="sql_exemplar",
            title=example.question,
            content=example.description,
            contextual_text=contextual_text,
            metadata=metadata,
        )
        content_hash = projection_content_hash(
            {
                "resource_type": RetrievalResourceType.SQL_EXEMPLAR.value,
                "source_resource_id": str(example.id),
                "dataset_id": example.dataset_id,
                "title": example.question,
                "metadata": metadata,
                "acl": acl,
                "visibility": "tenant",
                "units": [
                    {"unit_key": unit.unit_key, "content_hash": unit.content_hash}
                ],
            }
        )
        return ProjectedResource(
            tenant_id=workspace_id,
            namespace=namespace,
            resource_type=RetrievalResourceType.SQL_EXEMPLAR,
            source_type=RetrievalSourceType.SQL_EXEMPLAR,
            source_resource_id=str(example.id),
            dataset_id=example.dataset_id,
            title=example.question,
            metadata=metadata,
            acl=acl,
            visibility="tenant",
            source_version=source_version,
            content_hash=content_hash,
            units=(unit,),
        )


def _semantic_plan_summary(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """提取可进入规划上下文的语义要点，明确排除 SQL 与物理表信息。"""

    if not plan:
        return None
    metrics = [
        item.get("metric_id")
        for item in plan.get("metrics") or []
        if isinstance(item, dict) and item.get("metric_id") is not None
    ]
    dimensions = [
        item.get("logical_dimension_id") or item.get("physical_dimension_id")
        for item in plan.get("dimensions") or []
        if isinstance(item, dict)
        and (
            item.get("logical_dimension_id") is not None
            or item.get("physical_dimension_id") is not None
        )
    ]
    time_binding = plan.get("time_binding")
    return {
        "metric_ids": metrics,
        "dimension_ids": dimensions,
        "filters": [
            {
                key: item[key]
                for key in ("physical_dimension_id", "operator", "value")
                if key in item
            }
            for item in plan.get("filters") or []
            if isinstance(item, dict)
        ],
        "time_range": (
            time_binding.get("time_range")
            if isinstance(time_binding, dict)
            else None
        ),
        "query_shape": plan.get("query_shape") or {},
        "order_by": plan.get("order_by") or [],
        "limit": plan.get("limit"),
    }


__all__ = ["SQLExampleSourceProjector"]
