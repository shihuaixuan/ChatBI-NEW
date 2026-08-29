"""候选资产检索服务的边界测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.retrieval import filter_semantic_payload_tables
from apps.retrieval.errors import RetrievalQueryError
from apps.retrieval.models.dto import (
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalRequest,
    RetrievalScope,
)
from apps.retrieval.projection.planner import SemanticBindingQueryPlanner
from apps.retrieval.query.hybrid import HybridRecallResult
from apps.retrieval.query.semantic_binding import (
    SEMANTIC_BINDING_STRATEGY_VERSION,
    CandidateRetrievalExecutionResult,
)
from apps.retrieval.query.service import (
    RetrievalService,
    build_retrieval_request,
)


def _request(strategy_version: str | None = None) -> RetrievalRequest:
    return build_retrieval_request(
        request_id="request-1",
        tenant_id=1,
        actor_id=2,
        dataset_id=20,
        metric_phrases=["GMV"],
        dimension_phrases=[],
        principal_roles=["analyst"],
        principal_role_ids=[10],
        permission_version="permission-3",
        source_ids=["dataset:20"],
        strategy_version=strategy_version,
    )


def _execution(request: RetrievalRequest) -> CandidateRetrievalExecutionResult:
    plan = SemanticBindingQueryPlanner().plan(request)
    recall = HybridRecallResult(
        request_id=request.request_id,
        plan=plan,
        slots=(),
    )
    return CandidateRetrievalExecutionResult(
        recall=recall,
        payload={
            "hit": False,
            "status": "missed",
            "candidate_groups": {"metrics": [], "dimensions": []},
            "selected_assets": {},
            "decision": {},
            "retrieval_strategy_version": SEMANTIC_BINDING_STRATEGY_VERSION,
        },
        filters={"plan_fingerprint": plan.fingerprint},
    )


class _Runner:
    def __init__(self) -> None:
        self.calls = []

    def retrieve_candidates(self, session, request, strategy_version, timeout_ms):
        self.calls.append((session, request, strategy_version, timeout_ms))
        return _execution(request)

    def bind(self, session, request, recall, timeout_ms):
        return SimpleNamespace(
            payload={
                "status": "missed",
                "candidate_groups": {"metrics": [], "dimensions": []},
                "selected_assets": {},
                "decision": {},
                "retrieval_strategy_version": SEMANTIC_BINDING_STRATEGY_VERSION,
            },
            filters={"plan_fingerprint": recall.plan.fingerprint},
        )


def test_request_factory_contains_only_candidate_retrieval_fields():
    request = _request()

    assert request.strategy_version == SEMANTIC_BINDING_STRATEGY_VERSION
    assert request.scope.principal_roles == ["analyst"]
    assert request.scope.principal_role_ids == [10]
    assert request.scope.permission_version == "permission-3"
    assert request.scope.source_ids == ["dataset:20"]
    assert "intent" not in request.model_dump()
    assert "rewrite_question" not in request.model_dump()


def test_retrieval_request_rejects_binding_fields():
    with pytest.raises(ValidationError):
        RetrievalRequest(
            request_id="request-1",
            tenant_id=1,
            actor_id=2,
            rewrite_question="GMV",
            metric_phrases=["GMV"],
            dimension_phrases=[],
            intent=RetrievalIntent(intent_type="metric_query"),
            scope=RetrievalScope(dataset_ids=[20]),
            profiles=[RetrievalProfileName.SEMANTIC_BINDING],
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
        )


def test_request_factory_rejects_invalid_strategy_version():
    service = RetrievalService(object(), semantic_binding_runner=_Runner())

    with pytest.raises(RetrievalQueryError, match="仅支持 semantic-binding"):
        service.retrieve(_request("semantic-binding-v1"))


def test_service_executes_candidate_retrieval_only():
    session = object()
    runner = _Runner()
    service = RetrievalService(session, semantic_binding_runner=runner, query_timeout_ms=1200)

    result = service.retrieve(_request())

    assert result.payload["status"] == "missed"
    assert result.payload["decision"] == {}
    assert result.filters == {"plan_fingerprint": result.recall.plan.fingerprint}
    assert runner.calls == [
        (session, _request(), SEMANTIC_BINDING_STRATEGY_VERSION, 1200)
    ]




def test_semantic_payload_filter_removes_nested_unauthorized_tables():
    payload = {
        "tables": ["orders", "secret_orders"],
        "candidate_groups": {
            "metrics": [
                {"biz_name": "amount", "physical_table": "orders"},
                {"biz_name": "secret", "physical_table": "secret_orders"},
            ]
        },
    }

    result = filter_semantic_payload_tables(payload, ["orders"])

    assert result["tables"] == ["orders"]
    assert result["candidate_groups"]["metrics"] == [
        {"biz_name": "amount", "physical_table": "orders"}
    ]
