from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticQueryCompileRequest,
)
from apps.semantic.services.sql_compilation_service import (
    SemanticSQLCompilationService,
)
from apps.semantic.services.sql_compiler import SemanticSQLCompileResult


def _schema() -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business",
            type="DATASET",
        )
    )


class StaticSchemaProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DatasetSchema:
        self.calls.append((oid, dataset_id))
        return _schema()


class CapturingCompiler:
    def __init__(self) -> None:
        self.requests = []

    def compile(self, request):
        self.requests.append(request)
        return SemanticSQLCompileResult(
            sql="select 1",
            tables=["orders"],
            metrics=["gmv"],
            dimensions=["city"],
        )


def test_compilation_service_loads_schema_and_forwards_complete_plan():
    schema_provider = StaticSchemaProvider()
    compiler = CapturingCompiler()
    service = SemanticSQLCompilationService(schema_provider, compiler)

    result = service.compile(
        SemanticQueryCompileRequest(
            workspace_id=10,
            dataset_id=20,
            question="按城市看销售额",
            slots={"metrics": [{"asset_id": 100}]},
            order_by=[{"biz_name": "gmv", "direction": "desc"}],
            limit=10,
            time_bucket={"asset_id": 200, "grain": "month"},
        )
    )

    assert schema_provider.calls == [(10, 20)]
    assert compiler.requests[0].question == "按城市看销售额"
    assert compiler.requests[0].limit == 10
    assert compiler.requests[0].time_bucket == {
        "asset_id": 200,
        "grain": "month",
    }
    assert result.sql == "select 1"
    assert result.schema.data_set.id == 20
