"""语义编译组装契约测试（逻辑移自 v1 SemanticSQLCompilerTool，行为不得回归）。"""

from unittest.mock import MagicMock, patch

from apps.capabilities.semantic.compile import compile_semantic_sql


def test_requires_dataset_or_datasource():
    result = compile_semantic_sql(session=None, oid=1)
    assert not result.success
    assert result.error_code == "dataset_or_datasource_required"


def test_reports_missing_semantic_dataset():
    with patch(
        "apps.capabilities.semantic.compile.resolve_dataset_by_datasource",
        return_value=None,
    ):
        result = compile_semantic_sql(session=None, oid=1, datasource_id=5)
    assert not result.success
    assert result.error_code == "semantic_dataset_not_found"


def test_compiles_with_explicit_dataset_id():
    compiler = MagicMock()
    compiler.compile.return_value = MagicMock(
        sql="select 1", tables=["t"], metrics=["gmv"], dimensions=["city"]
    )
    with patch("apps.capabilities.semantic.compile.SemanticSchemaBuilder") as builder:
        builder.return_value.build_dataset_schema.return_value = MagicMock()
        result = compile_semantic_sql(
            session=None,
            oid=1,
            dataset_id=9,
            question="按城市看 gmv",
            slots={"metrics": ["gmv"]},
            limit=100,
            compiler=compiler,
        )

    assert result.success
    assert result.payload["sql"] == "select 1"
    assert result.payload["dataset_id"] == 9
    assert result.payload["strategy"] == "semantic_sql_compiler"
    request = compiler.compile.call_args.args[0]
    assert request.question == "按城市看 gmv"
    assert request.slots == {"metrics": ["gmv"]}
    assert request.limit == 100


def test_translates_compiler_value_error():
    compiler = MagicMock()
    compiler.compile.side_effect = ValueError("metric_not_found")
    with patch("apps.capabilities.semantic.compile.SemanticSchemaBuilder") as builder:
        builder.return_value.build_dataset_schema.return_value = MagicMock()
        result = compile_semantic_sql(session=None, oid=1, dataset_id=9, compiler=compiler)

    assert not result.success
    assert result.error_code == "metric_not_found"
