"""统一投影契约的来源解耦与 scope 约束测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.retrieval.projection import ProjectedResource, ProjectedUnit
from apps.retrieval.schemas import RetrievalResourceType, RetrievalSourceType


def _knowledge_resource(**overrides) -> ProjectedResource:
    values = {
        "tenant_id": 1,
        "namespace": "knowledge:policy",
        "resource_type": RetrievalResourceType.KNOWLEDGE_CHUNK,
        "source_type": RetrievalSourceType.KNOWLEDGE_BASE,
        "source_resource_id": "file:1:chunk:1",
        "knowledge_base_id": 10,
        "title": "退款制度",
        "source_version": "file-v1",
        "content_hash": "a" * 64,
        "units": (
            ProjectedUnit.create(
                unit_key="chunk:1",
                content_kind="paragraph",
                title="退款制度",
                content="订单支付后七天内可以申请退款。",
            ),
        ),
    }
    values.update(overrides)
    return ProjectedResource.model_validate(values)


def test_projection_contract_supports_non_semantic_knowledge_scope():
    resource = _knowledge_resource()

    assert resource.source_type == RetrievalSourceType.KNOWLEDGE_BASE
    assert resource.dataset_id is None
    assert resource.knowledge_base_id == 10


def test_projection_contract_rejects_mixed_dataset_and_knowledge_scope():
    with pytest.raises(ValidationError, match="不能同时属于数据集和知识库"):
        _knowledge_resource(dataset_id=20)
