from types import SimpleNamespace

from apps.chatbi.models import SemanticRetrievalData
from apps.chatbi.services.planning import SemanticRetrievalService


class RecordingRetrievalGateway:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        return SimpleNamespace(payload=self.payload)


def test_semantic_retrieval_service_builds_one_shared_request():
    gateway = RecordingRetrievalGateway({"status": "hit"})
    service = SemanticRetrievalService(gateway)

    result = service.retrieve(
        SemanticRetrievalData(
            workspace_id=10,
            user_id=30,
            dataset_id=20,
            original_question="原始问题",
            rewritten_question="改写问题",
            intent={"metric_mentions": ["销售额"]},
            request_id="run-1",
        )
    )

    request = gateway.requests[0]
    assert result == {"status": "hit"}
    assert request.request_id == "run-1"
    assert request.tenant_id == 10
    assert request.actor_id == 30
    assert request.scope.dataset_ids == [20]
    assert request.original_question == "原始问题"
    assert request.rewritten_question == "改写问题"


def test_agent_projection_trims_candidates_and_preserves_decision():
    service = SemanticRetrievalService(
        RecordingRetrievalGateway(
            {
                "hit": True,
                "status": "hit",
                "decision": {"status": "accepted"},
                "candidate_groups": {
                    "metrics": [
                        {
                            "asset_id": index,
                            "asset_type": "METRIC",
                            "biz_name": f"metric_{index}",
                            "internal": "hidden",
                        }
                        for index in range(7)
                    ]
                },
            }
        )
    )

    result = service.retrieve_for_agent(
        SemanticRetrievalData(
            workspace_id=10,
            user_id=30,
            dataset_id=20,
            original_question="销售额",
            rewritten_question="销售额",
        ),
        max_candidates_per_group=5,
    )

    assert len(result["candidate_groups"]["metrics"]) == 5
    assert result["truncated"] == {"metrics": 2}
    assert "internal" not in result["candidate_groups"]["metrics"][0]
    assert result["decision"] == {"status": "accepted"}


def test_semantic_retrieval_filters_unauthorized_tables_in_nested_assets():
    package = {
        "tables": ["orders", "secret_orders"],
        "candidate_groups": {
            "metrics": [
                {"biz_name": "amount", "physical_table": "orders"},
                {
                    "biz_name": "secret_amount",
                    "physical_table": "secret_orders",
                },
            ]
        },
        "slot_bindings": {
            "metrics": [
                {"biz_name": "amount", "table_name": "orders"},
                {
                    "biz_name": "secret_amount",
                    "table_name": "secret_orders",
                },
            ]
        },
    }

    result = SemanticRetrievalService.filter_authorized_tables(
        package,
        ["orders"],
    )

    assert result["tables"] == ["orders"]
    assert result["candidate_groups"]["metrics"] == [
        {"biz_name": "amount", "physical_table": "orders"}
    ]
    assert result["slot_bindings"]["metrics"] == [
        {"biz_name": "amount", "table_name": "orders"}
    ]
