"""P0-7 检索 ACL：请求侧身份透传与索引可见性结构。"""

from __future__ import annotations

from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.retrieval.models.dto import RetrievalResourceType, RetrievalSourceType
from apps.retrieval.query.hybrid import _EXACT_SQL
from apps.retrieval.sources.semantic_projector import (
    SemanticProjectionPolicy,
    SemanticSourceProjector,
)
from apps.semantic.models.dto import DatasetSchema, SchemaElement


def _understanding() -> dict:
    return {
        "rewrite_question": "本月销售额",
        "intent": {"intent_type": "metric_query", "metric_mentions": ["销售额"]},
    }


def _context(**overrides) -> AgentToolContext:
    context = AgentToolContext(
        session=None,
        oid=1,
        user_id=7,
        datasource_id=5,
        dataset_id=20,
        state={"question_understanding": _understanding()},
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context


def test_retrieval_request_carries_principal_identity():
    context = _context(
        principal_roles=["analyst"],
        principal_role_ids=[301],
        permission_version="permission-3",
    )

    request = context.semantic_retrieval_request

    assert request is not None
    assert request.actor_id == 7
    assert request.scope.principal_roles == ["analyst"]
    assert request.scope.principal_role_ids == [301]
    assert request.scope.permission_version == "permission-3"


def test_retrieval_request_defaults_to_tenant_scope_without_identity():
    request = _context().semantic_retrieval_request

    assert request is not None
    assert request.scope.principal_roles == []
    assert request.scope.principal_role_ids == []
    assert request.scope.permission_version is None


def _schema() -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        ),
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="销售额",
                biz_name="sales_amount",
                type="METRIC",
            )
        ],
    )


def test_projector_writes_tenant_visibility_into_resources():
    resources = SemanticSourceProjector(SemanticProjectionPolicy()).project(
        _schema(),
        tenant_id=1,
        namespace="headless:dataset:20",
        source_version="schema:1:index:1",
        acl={"visibility": "tenant", "actor_ids": [], "roles": [], "role_ids": []},
        visibility="tenant",
    )

    assert resources
    assert all(item.visibility == "tenant" for item in resources)
    assert all(item.acl == {"visibility": "tenant", "actor_ids": [], "roles": [], "role_ids": []} for item in resources)


def test_projector_supports_private_visibility_with_actor_grant():
    acl = {
        "visibility": "private",
        "actor_ids": [7],
        "roles": [],
        "role_ids": [],
    }
    resources = SemanticSourceProjector(SemanticProjectionPolicy()).project(
        _schema(),
        tenant_id=1,
        namespace="headless:dataset:20",
        source_version="schema:1:index:1",
        acl=acl,
        visibility="private",
    )

    metric_resource = next(
        item for item in resources if item.resource_type == RetrievalResourceType.METRIC
    )
    assert metric_resource.visibility == "private"
    assert metric_resource.acl == acl


def test_hybrid_sql_enforces_visibility_and_principal_predicates():
    """查询端 SQL 必须保留可见性与 actor/角色谓词，防止 ACL 接通被静默移除。"""

    sql_text = str(_EXACT_SQL)
    assert "r.visibility IN ('public', 'tenant')" in sql_text
    assert "r.visibility = 'private'" in sql_text
    assert ":actor_id" in sql_text
    assert ":principal_roles" in sql_text
    assert ":principal_role_ids" in sql_text
    assert ":permission_version" in sql_text


def test_sql_example_source_type_still_available_for_exemplar_profile():
    """知识来源类型保留（KNOWLEDGE_BASE scope 仍可用），只移除了未实现的 profile。"""

    assert RetrievalSourceType.KNOWLEDGE_BASE.value == "knowledge_base"
