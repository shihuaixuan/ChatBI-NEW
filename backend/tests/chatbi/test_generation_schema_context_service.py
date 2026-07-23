from typing import Any, cast

import pytest

from apps.access_control.models.dto import (
    DataPolicy,
    DataPolicyDeniedColumn,
    UserInfoDTO,
)
from apps.access_control.services import DataPolicyService
from apps.chatbi.adapters import embedding_ranking as ranking_module
from apps.chatbi.adapters.embedding_ranking import (
    EmbeddingSchemaRankingClient,
)
from apps.chatbi.models import GenerationSchemaTableCandidate
from apps.chatbi.services.generation import (
    SchemaContextService,
    SchemaRankingClient,
)
from apps.datasource import DatasourceRecord, PhysicalTableDetail
from apps.datasource.models.dto import PhysicalField, PhysicalTable
from apps.datasource.services import (
    DatasourceConnectionService,
    DatasourceMetadataService,
    DatasourceService,
)


class FakeDatasourceService:
    def get(self, datasource_id: int) -> DatasourceRecord:
        assert datasource_id == 20
        return DatasourceRecord(
            id=20,
            type="pg",
            table_relation=[
                {
                    "id": "relation-1",
                    "shape": "edge",
                    "source": {"cell": 11, "port": 110},
                    "target": {"cell": 10, "port": 100},
                }
            ],
        )


class FakeMetadataService:
    def get_schema(self, datasource_id: int) -> list[PhysicalTableDetail]:
        assert datasource_id == 20
        return [
            PhysicalTableDetail(
                table=PhysicalTable(
                    id=10,
                    ds_id=20,
                    table_name="orders",
                    custom_comment="订单",
                    embedding="[1, 0]",
                ),
                fields=[
                    PhysicalField(
                        id=100,
                        table_id=10,
                        field_name="customer_id",
                        field_type="bigint",
                        custom_comment="客户ID",
                    ),
                    PhysicalField(
                        id=101,
                        table_id=10,
                        field_name="internal_note",
                        field_type="text",
                    ),
                ],
            ),
            PhysicalTableDetail(
                table=PhysicalTable(
                    id=11,
                    ds_id=20,
                    table_name="customers",
                    custom_comment="客户",
                    embedding="[0, 1]",
                ),
                fields=[
                    PhysicalField(
                        id=110,
                        table_id=11,
                        field_name="id",
                        field_type="bigint",
                    )
                ],
            ),
        ]


class FakeConnectionService:
    def __init__(self, *, fail_sample: bool = False) -> None:
        self.fail_sample = fail_sample
        self.sample_calls: list[tuple[str, list[str]]] = []

    def get_database_name(self, datasource_id: int) -> str:
        assert datasource_id == 20
        return "public"

    def sample_rows(
        self,
        datasource_id: int,
        table_name: str,
        field_names: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        assert datasource_id == 20
        assert limit == 3
        self.sample_calls.append((table_name, field_names))
        if self.fail_sample:
            raise RuntimeError("样例查询失败")
        return [{field_names[0]: "a" * 101 + "\n"}]


class FakeDataPolicyService:
    def resolve(self, *_args, **_kwargs) -> DataPolicy:
        return DataPolicy(
            denied_columns=[
                DataPolicyDeniedColumn(
                    table_id=10,
                    table="orders",
                    field_id=101,
                    column="internal_note",
                )
            ]
        )


class FakeTableRanker:
    def __init__(self) -> None:
        self.candidates: list[GenerationSchemaTableCandidate] = []

    def rank(
        self,
        question: str,
        candidates: list[GenerationSchemaTableCandidate],
        *,
        limit: int,
    ) -> list[int]:
        assert question == "客户订单"
        assert limit == 10
        self.candidates = candidates
        return [11]


def _user() -> UserInfoDTO:
    return UserInfoDTO(
        id=2,
        account="member",
        oid=7,
        name="Member",
        email="member@example.com",
        status=1,
        origin=0,
    )


def _service(
    connection_service: FakeConnectionService,
) -> tuple[SchemaContextService, FakeTableRanker]:
    ranker = FakeTableRanker()
    return (
        SchemaContextService(
            cast(DatasourceService, FakeDatasourceService()),
            cast(DatasourceMetadataService, FakeMetadataService()),
            cast(DatasourceConnectionService, connection_service),
            cast(DataPolicyService, FakeDataPolicyService()),
            cast(SchemaRankingClient, ranker),
            embedding_enabled=True,
            embedding_limit=10,
        ),
        ranker,
    )


def test_generation_schema_context_applies_permission_ranking_and_relations():
    connection_service = FakeConnectionService()
    service, ranker = _service(connection_service)

    result = service.build(_user(), 20, "客户订单")

    assert [item.table_id for item in ranker.candidates] == [10, 11]
    assert result.schema.index("# Table: public.customers") < result.schema.index(
        "# Table: public.orders"
    )
    assert "customers.id=orders.customer_id" in result.schema
    assert "internal_note" not in result.schema
    assert connection_service.sample_calls == [
        ("orders", ["customer_id"]),
        ("customers", ["id"]),
    ]
    assert "a" * 100 + "..." in result.sample_data


def test_generation_schema_context_does_not_hide_sample_query_failure():
    service, _ = _service(FakeConnectionService(fail_sample=True))

    with pytest.raises(RuntimeError, match="样例查询失败"):
        service.build(_user(), 20, "客户订单")


def test_embedding_table_ranker_orders_candidates_by_similarity(monkeypatch):
    class FakeEmbeddingModel:
        def embed_query(self, _question: str) -> list[float]:
            return [0.0, 1.0]

    monkeypatch.setattr(
        ranking_module.EmbeddingModelCache,
        "get_model",
        staticmethod(lambda: FakeEmbeddingModel()),
    )

    result = EmbeddingSchemaRankingClient().rank(
        "客户订单",
        [
            GenerationSchemaTableCandidate(10, "[1, 0]"),
            GenerationSchemaTableCandidate(11, "[0, 1]"),
        ],
        limit=1,
    )

    assert result == [11]
