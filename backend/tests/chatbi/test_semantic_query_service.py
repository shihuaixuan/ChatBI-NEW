import pytest

from apps.chatbi.models import SemanticQueryCompileData
from apps.chatbi.services import SemanticQueryCompileError, SemanticQueryService
from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticQueryCompileResult,
)


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
        models=[{"id": 10, "datasource_id": 5}],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="销售额",
                biz_name="gmv",
                type="METRIC",
            )
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=200,
                name="城市",
                biz_name="city",
                type="DIMENSION",
            )
        ],
    )


class StaticCompilationGateway:
    def compile(self, request):
        return SemanticQueryCompileResult(
            dataset_id=request.dataset_id,
            sql="select city, sum(gmv) from orders group by city",
            tables=["orders"],
            metrics=["gmv"],
            dimensions=["city"],
            schema=_schema(),
        )


class FailingCompilationGateway:
    def compile(self, request):
        raise ValueError("SEMANTIC_SQL_ASSET_REQUIRED")


def test_semantic_query_service_projects_datasource_and_used_assets():
    result = SemanticQueryService(StaticCompilationGateway()).compile(
        SemanticQueryCompileData(
            workspace_id=10,
            dataset_id=20,
            question="按城市看销售额",
        )
    )

    assert result.datasource_id == 5
    assert [asset.asset_id for asset in result.used_assets] == [100, 200]


def test_semantic_query_service_preserves_compile_error_code():
    with pytest.raises(
        SemanticQueryCompileError,
        match="SEMANTIC_SQL_ASSET_REQUIRED",
    ):
        SemanticQueryService(FailingCompilationGateway()).compile(
            SemanticQueryCompileData(workspace_id=10, dataset_id=20)
        )
