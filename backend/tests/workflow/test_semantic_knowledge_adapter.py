"""Workflow 统一检索适配器的边界契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from apps.chatbi.orchestration.graph.capabilities.adapters.knowledge import (
    SemanticKnowledgeAdapter,
)
from apps.chatbi.orchestration.graph.capabilities.interactions import (
    apply_slot_response_to_intent,
)
from apps.retrieval.errors import RetrievalConfigurationError, RetrievalQueryError
from apps.retrieval.query.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
from apps.retrieval.query.service import RetrievalService


class _RecordingRetrievalService:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"status": "missed"}
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        return SimpleNamespace(payload=self.payload)


def _adapter(service: _RecordingRetrievalService) -> SemanticKnowledgeAdapter:
    # 测试替身只实现适配器依赖的 retrieve 协议。
    return SemanticKnowledgeAdapter(cast(RetrievalService, service))


def test_apply_slot_response_to_intent_sets_dimension_filter_value():
    intent = {
        "ambiguous_slots": ["dimension"],
        "dimension_mentions": ["店铺"],
        "dimension_slots": [
            {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"}
        ],
    }

    updated = apply_slot_response_to_intent(
        intent,
        {"dimension_usage": "filter_value_required", "dimension_values": {"店铺": "店铺为1"}},
    )

    assert updated["ambiguous_slots"] == []
    assert updated["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}
    ]
    assert intent["dimension_slots"][0]["value"] is None


def test_apply_slot_response_to_intent_sets_subject_domain():
    updated = apply_slot_response_to_intent(
        {"ambiguous_slots": ["subject_domain"]},
        {"domain_id": "7", "domain_name": "交易域"},
    )

    assert updated["subject_domain"]["domain_id"] == 7
    assert updated["subject_domain"]["domain_name"] == "交易域"
    assert updated["ambiguous_slots"] == []


def test_adapter_projects_workflow_context_to_semantic_binding_request():
    service = _RecordingRetrievalService({"status": "hit"})
    adapter = _adapter(service)

    result = adapter.retrieve(
        {
            "run_id": "run-42",
            "request": {
                "question": "原始问题",
                "dataset_id": 20,
                "tenant_id": 10,
                "user_id": 30,
            },
            "variables": {
                "rewrite": {"rewrite_question": "改写后的销售额"},
                "intent": {
                    "intent_type": "metric_query",
                    "metric_mentions": ["销售额"],
                },
            },
        }
    )

    request = service.requests[0]
    assert result == {"status": "hit"}
    assert request.request_id == "run-42"
    assert request.tenant_id == 10
    assert request.actor_id == 30
    assert request.original_question == "原始问题"
    assert request.rewritten_question == "改写后的销售额"
    assert request.scope.dataset_ids == [20]
    assert request.intent.metric_mentions == ["销售额"]
    assert request.strategy_version == SEMANTIC_BINDING_STRATEGY_VERSION


def test_adapter_applies_clarification_response_before_retrieval():
    service = _RecordingRetrievalService()
    adapter = _adapter(service)

    adapter.retrieve(
        {
            "request": {
                "question": "今天店铺的访问人数",
                "dataset_id": 20,
                "tenant_id": 10,
                "user_id": 30,
            },
            "variables": {
                "intent": {
                    "intent_type": "metric_query",
                    "dimension_mentions": ["店铺"],
                    "dimension_slots": [
                        {
                            "name": "店铺",
                            "role": "ambiguous",
                            "value": None,
                            "value_status": "not_provided",
                        }
                    ],
                    "ambiguous_slots": ["dimension"],
                },
                "interactions": {
                    "ask_slot_clarification": {
                        "response": {
                            "dimension_usage": "filter_value_required",
                            "dimension_values": {"店铺": "店铺为1"},
                        }
                    }
                },
            },
        }
    )

    intent = service.requests[0].intent
    assert intent.ambiguous_slots == []
    assert intent.dimension_slots[0].role == "filter"
    assert intent.dimension_slots[0].value == "1"
    assert intent.dimension_slots[0].value_status == "provided"


def test_adapter_requires_unified_retrieval_service():
    with pytest.raises(RetrievalConfigurationError) as exc_info:
        SemanticKnowledgeAdapter().retrieve(
            {"request": {"question": "销售额", "dataset_id": 20}}
        )

    assert exc_info.value.details["reason_code"] == "RETRIEVAL_SERVICE_MISSING"


@pytest.mark.parametrize(
    "workflow_request",
    [
        {"request": {"dataset_id": 20}},
        {"request": {"question": "销售额"}},
    ],
)
def test_adapter_rejects_incomplete_semantic_binding_request(workflow_request):
    with pytest.raises(RetrievalQueryError) as exc_info:
        _adapter(_RecordingRetrievalService()).retrieve(workflow_request)

    assert exc_info.value.details["reason_code"] == "SEMANTIC_BINDING_REQUEST_INCOMPLETE"
