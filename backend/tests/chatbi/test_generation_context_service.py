import json
from types import SimpleNamespace
from typing import Any, cast

from apps.chatbi.models import GenerationContextScope
from apps.chatbi.services import GenerationContextService
from apps.knowledge.services import SQLExampleQueryService
from apps.semantic.services import SemanticTermQueryService


class FakeSQLExampleQueryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, int | None]]] = []

    def build_prompt(
        self,
        question: str,
        workspace_id: int,
        **scope: int | None,
    ) -> tuple[str, list[dict[str, str]]]:
        self.calls.append((question, workspace_id, scope))
        return "sql examples", [{"question": question, "sql": "select 1"}]


class FakeSemanticTermQueryService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, int]] = []

    def search(
        self,
        workspace_id: int,
        dataset_id: int,
        question: str,
        limit: int = 10,
    ) -> list[Any]:
        self.calls.append((workspace_id, dataset_id, question, limit))
        return [SimpleNamespace(words=["销售额", "GMV"], description=None)]


def _service() -> tuple[
    GenerationContextService,
    FakeSQLExampleQueryService,
    FakeSemanticTermQueryService,
]:
    sql_examples = FakeSQLExampleQueryService()
    terms = FakeSemanticTermQueryService()
    service = GenerationContextService(
        cast(SQLExampleQueryService, sql_examples),
        cast(SemanticTermQueryService, terms),
    )
    return service, sql_examples, terms


def test_build_sql_examples_calls_knowledge_service_with_datasource_scope():
    service, sql_examples, _ = _service()

    result = service.build_sql_examples(
        "订单数",
        GenerationContextScope(workspace_id=10, datasource_id=20),
    )

    assert result[0] == "sql examples"
    assert sql_examples.calls == [
        ("订单数", 10, {"datasource_id": 20})
    ]


def test_build_sql_examples_calls_knowledge_service_with_assistant_scope():
    service, sql_examples, _ = _service()

    service.build_sql_examples(
        "订单数",
        GenerationContextScope(
            workspace_id=10,
            datasource_id=None,
            sql_example_assistant_id=30,
            use_assistant_sql_examples=True,
        ),
    )

    assert sql_examples.calls == [
        ("订单数", 10, {"assistant_id": 30})
    ]


def test_build_term_context_calls_semantic_service_and_projects_prompt():
    service, _, terms = _service()

    prompt, items = service.build_term_context(10, 20, "GMV是多少")

    assert terms.calls == [(10, 20, "GMV是多少", 10)]
    assert json.loads(prompt) == items == [
        {
            "words": ["销售额", "GMV"],
            "description": "",
        }
    ]


def test_build_term_context_skips_unbound_dataset():
    service, _, terms = _service()

    assert service.build_term_context(10, None, "GMV是多少") == ("", [])
    assert terms.calls == []
