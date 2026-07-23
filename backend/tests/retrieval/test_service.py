"""RetrievalService 的唯一入口一致性测试。"""

from __future__ import annotations

import pytest

from apps.chatbi.models import SemanticRetrievalData
from apps.chatbi.orchestration.graph.capabilities.adapters.knowledge import (
    SemanticKnowledgeAdapter,
)
from apps.chatbi.services.planning import SemanticRetrievalService
from apps.retrieval.errors import RetrievalQueryError
from apps.retrieval.models.dto import (
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
)
from apps.retrieval.semantic_binding import (
    SEMANTIC_BINDING_STRATEGY_VERSION,
    SemanticBindingExecutionResult,
)
from apps.retrieval.service import RetrievalService, build_semantic_binding_request


def _request(strategy_version: str | None = None):
    return build_semantic_binding_request(
        request_id="request-1",
        tenant_id=1,
        actor_id=2,
        dataset_id=20,
        original_question="GMV",
        rewritten_question="GMV",
        intent={"intent_type": "metric_query", "metric_mentions": ["GMV"]},
        principal_roles=["analyst"],
        principal_role_ids=[10],
        permission_version="permission-3",
        source_ids=["dataset:20"],
        strategy_version=strategy_version,
    )


def _execution(request_id: str = "request-1") -> SemanticBindingExecutionResult:
    bundle = RetrievalBundle(
        request_id=request_id,
        bindings=RetrievalBindings(),
        decision=RetrievalDecision(status=RetrievalDecisionStatus.MISSED),
        diagnostics=RetrievalDiagnostics(
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
            index_generation="generation-2",
        ),
    )
    return SemanticBindingExecutionResult(
        bundle=bundle,
        payload={
            "hit": False,
            "status": "missed",
            "decision": {"status": "missed", "strategy": "semantic_binding"},
            "candidate_groups": {
                "metrics": [],
                "dimensions": [],
                "values": [],
                "terms": [],
            },
            "selected_assets": {
                "metrics": [],
                "dimensions": [],
                "values": [],
                "terms": [],
            },
            "retrieval_strategy_version": SEMANTIC_BINDING_STRATEGY_VERSION,
            "retrieval_diagnostics": bundle.diagnostics.model_dump(mode="json"),
        },
        filters={"plan_fingerprint": "plan-1"},
    )


class _Runner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, session, request, strategy_version, timeout_ms):
        self.calls.append((session, request, strategy_version, timeout_ms))
        return _execution(request.request_id)


def test_request_factory_defaults_to_semantic_binding_and_carries_acl_context():
    request = _request()

    assert request.strategy_version == SEMANTIC_BINDING_STRATEGY_VERSION
    assert request.scope.principal_roles == ["analyst"]
    assert request.scope.principal_role_ids == [10]
    assert request.scope.permission_version == "permission-3"
    assert request.scope.source_ids == ["dataset:20"]


def test_request_factory_projects_question_understanding_dimension_slot():
    request = build_semantic_binding_request(
        request_id="request-dimension-slot",
        tenant_id=1,
        actor_id=2,
        dataset_id=20,
        original_question="按店铺看 GMV",
        rewritten_question="按店铺看 GMV",
        intent={
            "intent_type": "metric_query",
            "metric_mentions": ["GMV"],
            "dimension_slots": [
                {
                    "name": "店铺",
                    "role": "group_by",
                    "value": None,
                    "value_status": "not_provided",
                    "value_confidence": 0.0,
                }
            ],
        },
    )

    assert request.intent.dimension_slots[0].model_dump() == {
        "name": "店铺",
        "role": "group_by",
        "value": None,
        "value_status": "not_provided",
    }


def test_request_factory_rejects_unknown_dimension_slot_fields():
    with pytest.raises(RetrievalQueryError) as exc_info:
        build_semantic_binding_request(
            request_id="request-invalid-dimension-slot",
            tenant_id=1,
            actor_id=2,
            dataset_id=20,
            original_question="按店铺看 GMV",
            rewritten_question="按店铺看 GMV",
            intent={
                "intent_type": "metric_query",
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "group_by",
                        "unexpected_field": "unexpected",
                    }
                ],
            },
        )

    assert exc_info.value.details == {
        "reason_code": "DIMENSION_SLOT_FIELDS_UNSUPPORTED",
        "slot_index": 0,
        "fields": ["unexpected_field"],
    }


def test_v1_request_is_rejected_instead_of_falling_back():
    service = RetrievalService(object(), semantic_binding_runner=_Runner())

    with pytest.raises(RetrievalQueryError, match="仅支持 semantic-binding"):
        service.retrieve(_request("semantic-binding-v1"))


def test_service_executes_semantic_binding_as_the_only_strategy():
    session = object()
    runner = _Runner()
    service = RetrievalService(
        session,
        semantic_binding_runner=runner,
        query_timeout_ms=1200,
    )

    result = service.retrieve(_request())

    assert result.payload["decision"]["strategy"] == "semantic_binding"
    assert result.filters == {"plan_fingerprint": "plan-1"}
    assert runner.calls == [
        (session, _request(), SEMANTIC_BINDING_STRATEGY_VERSION, 1200)
    ]


def test_graph_and_agent_consume_the_same_semantic_binding_result():
    service = RetrievalService(object(), semantic_binding_runner=_Runner())
    graph = SemanticKnowledgeAdapter(retrieval_service=service).retrieve(
        {
            "run_id": "run-1",
            "request": {
                "question": "GMV",
                "dataset_id": 20,
                "tenant_id": 1,
                "user_id": 2,
            },
            "variables": {
                "intent": {"intent_type": "metric_query", "metric_mentions": ["GMV"]}
            },
        }
    )
    agent = SemanticRetrievalService(service).retrieve_for_agent(
        SemanticRetrievalData(
            workspace_id=1,
            user_id=2,
            dataset_id=20,
            original_question="GMV",
            rewritten_question="GMV",
            intent={
                "intent_type": "metric_query",
                "metric_mentions": ["GMV"],
            },
            request_id="run-1",
        )
    )

    assert graph["status"] == agent["status"] == "missed"
    assert graph["decision"] == agent["decision"]
    assert graph["retrieval_strategy_version"] == SEMANTIC_BINDING_STRATEGY_VERSION


@pytest.mark.parametrize(
    ("ambiguity_type", "expected_status"),
    [("metric", "metric_ambiguous"), ("dimension", "dimension_ambiguous")],
)
def test_agent_semantic_status_identifies_ambiguous_slot_type(
    ambiguity_type: str,
    expected_status: str,
):
    status = SemanticRetrievalService.agent_semantic_status(
        {
            "status": "metric_ambiguous",
            "decision": {"status": "ambiguous"},
            "ambiguities": [{"type": ambiguity_type}],
        }
    )

    assert status == expected_status


def test_agent_semantic_status_reports_missing_time_dimension_configuration():
    status = SemanticRetrievalService.agent_semantic_status(
        {
            "status": "missed",
            "decision": {
                "status": "partial",
                "reason_codes": [
                    "SEMANTIC_BINDING_PARTIAL",
                    "TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL",
                ],
            },
        }
    )

    assert status == "time_dimension_not_configured"
