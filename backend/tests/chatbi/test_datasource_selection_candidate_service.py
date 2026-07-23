from typing import cast

import pytest

from apps.assistant import AssistantHeader
from apps.assistant.services import AssistantService
from apps.chatbi.adapters import embedding_ranking as ranking_module
from apps.chatbi.adapters.embedding_ranking import (
    EmbeddingDatasourceSelectionCandidateRanker,
)
from apps.chatbi.models import DatasourceSelectionRankingCandidate
from apps.chatbi.services.planning import (
    DatasourceSelectionCandidateRanker,
    DatasourceSelectionCandidateService,
    DatasourceSelectionError,
)
from apps.datasource import DatasourceRecord, DatasourceSummary, ExternalDatasource
from apps.datasource.services import DatasourceService


class FakeAssistantService:
    def __init__(self, summaries: list[DatasourceSummary]) -> None:
        self.summaries = summaries
        self.external_datasources: list[ExternalDatasource] | None = None

    def list_datasources(
        self,
        _assistant: AssistantHeader,
        *,
        external_datasources: list[ExternalDatasource] | None = None,
    ) -> list[DatasourceSummary]:
        self.external_datasources = external_datasources
        return self.summaries


class FakeDatasourceService:
    def __init__(self, records: list[DatasourceRecord]) -> None:
        self.records = records
        self.calls: list[int] = []

    def list_by_workspace(self, workspace_id: int) -> list[DatasourceRecord]:
        self.calls.append(workspace_id)
        return self.records


class FakeRanker:
    def __init__(self, result: list[int]) -> None:
        self.result = result
        self.candidates: list[DatasourceSelectionRankingCandidate] = []

    def rank(
        self,
        question: str,
        candidates: list[DatasourceSelectionRankingCandidate],
        *,
        limit: int,
    ) -> list[int]:
        assert question == "查询订单"
        assert limit == 2
        self.candidates = candidates
        return self.result


def _assistant(assistant_type: int) -> AssistantHeader:
    return AssistantHeader(
        id=10,
        name="销售助手",
        domain="https://example.com",
        type=assistant_type,
        configuration='{"oid": 7, "public_list": [1, 2]}',
        oid=7,
    )


def _service(
    assistant_service: FakeAssistantService,
    datasource_service: FakeDatasourceService,
    ranker: FakeRanker,
) -> DatasourceSelectionCandidateService:
    return DatasourceSelectionCandidateService(
        cast(AssistantService, assistant_service),
        cast(DatasourceService, datasource_service),
        cast(DatasourceSelectionCandidateRanker, ranker),
        embedding_enabled=True,
        embedding_limit=2,
    )


def test_local_candidates_are_ranked_with_datasource_embeddings():
    datasource_service = FakeDatasourceService(
        [
            DatasourceRecord(id=1, name="销售库", embedding="[1, 0]"),
            DatasourceRecord(id=2, name="订单库", embedding="[0, 1]"),
            DatasourceRecord(id=3, name="库存库", embedding=None),
        ]
    )
    ranker = FakeRanker([2, 1])
    service = _service(FakeAssistantService([]), datasource_service, ranker)

    result = service.list_candidates(7, None, "查询订单")

    assert [candidate.id for candidate in result] == [2, 1]
    assert [candidate.embedding for candidate in ranker.candidates] == [
        "[1, 0]",
        "[0, 1]",
        None,
    ]
    assert datasource_service.calls == [7]


def test_local_assistant_scope_uses_public_summaries_and_local_embeddings():
    assistant_service = FakeAssistantService(
        [
            DatasourceSummary(id=2, name="订单库"),
            DatasourceSummary(id=1, name="销售库"),
        ]
    )
    datasource_service = FakeDatasourceService(
        [
            DatasourceRecord(id=1, name="销售库", embedding="[1, 0]"),
            DatasourceRecord(id=2, name="订单库", embedding="[0, 1]"),
        ]
    )
    ranker = FakeRanker([1, 2])
    service = _service(assistant_service, datasource_service, ranker)

    result = service.list_candidates(7, _assistant(0), "查询订单")

    assert [candidate.id for candidate in result] == [1, 2]
    assert [candidate.embedding for candidate in ranker.candidates] == [
        "[0, 1]",
        "[1, 0]",
    ]


def test_external_candidates_keep_assistant_order_without_local_ranking():
    external_datasources = [
        ExternalDatasource(id=8, name="外部订单库", type="mysql"),
        ExternalDatasource(id=9, name="外部客户库", type="pg"),
    ]
    assistant_service = FakeAssistantService(
        [
            DatasourceSummary(id="8", name="外部订单库"),
            DatasourceSummary(id="9", name="外部客户库"),
        ]
    )
    datasource_service = FakeDatasourceService([])
    ranker = FakeRanker([])
    service = _service(assistant_service, datasource_service, ranker)

    result = service.list_candidates(
        7,
        _assistant(3),
        "查询订单",
        external_datasources=external_datasources,
    )

    assert [candidate.id for candidate in result] == [8, 9]
    assert assistant_service.external_datasources is external_datasources
    assert datasource_service.calls == []
    assert ranker.candidates == []


def test_local_assistant_candidate_must_exist_in_workspace_datasources():
    service = _service(
        FakeAssistantService([DatasourceSummary(id=9, name="越界数据源")]),
        FakeDatasourceService(
            [DatasourceRecord(id=1, name="销售库", embedding="[1, 0]")]
        ),
        FakeRanker([9]),
    )

    with pytest.raises(
        DatasourceSelectionError,
        match="DATASOURCE_SELECTION_LOCAL_CANDIDATE_NOT_FOUND:9",
    ):
        service.list_candidates(7, _assistant(0), "查询订单")


def test_datasource_candidate_ranker_does_not_hide_invalid_embedding(monkeypatch):
    class FakeEmbeddingModel:
        def embed_query(self, _question: str) -> list[float]:
            return [0.0, 1.0]

    monkeypatch.setattr(
        ranking_module.EmbeddingModelCache,
        "get_model",
        staticmethod(lambda: FakeEmbeddingModel()),
    )

    with pytest.raises(ValueError, match="CHATBI_CANDIDATE_EMBEDDING_INVALID"):
        EmbeddingDatasourceSelectionCandidateRanker().rank(
            "查询订单",
            [DatasourceSelectionRankingCandidate(id=1, embedding='{"x": 1}')],
            limit=1,
        )
