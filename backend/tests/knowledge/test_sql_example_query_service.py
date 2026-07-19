from apps.knowledge.models.dto import SQLExampleMatch
from apps.knowledge.services import SQLExampleQueryService


class QueryRepository:
    def __init__(self) -> None:
        self.lexical_ids = [1, 2]
        self.search_calls = 0

    def search_lexical_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        _ = workspace_id, question, datasource_id, assistant_id
        self.search_calls += 1
        return list(self.lexical_ids)

    def get_matches(self, example_ids: list[int]) -> list[SQLExampleMatch]:
        return [
            SQLExampleMatch(
                id=example_id,
                question=f"问题{example_id}",
                suggestion_answer=f"SELECT {example_id}",
            )
            for example_id in example_ids
        ]


class VectorSearch:
    def search_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        _ = workspace_id, question, datasource_id, assistant_id
        return [2, 3]


def test_query_merges_lexical_and_vector_results_without_duplicates():
    repository = QueryRepository()
    service = SQLExampleQueryService(repository, VectorSearch())

    result = service.search("销售额", 3, datasource_id=8)

    assert [item["question"] for item in result] == ["问题1", "问题2", "问题3"]
    assert result[0]["suggestion-answer"] == "SELECT 1"


def test_blank_question_does_not_query_repository():
    repository = QueryRepository()
    service = SQLExampleQueryService(repository)

    assert service.search("  ", 3, datasource_id=8) == []
    assert repository.search_calls == 0


def test_prompt_keeps_existing_sql_examples_xml_contract():
    service = SQLExampleQueryService(QueryRepository())

    prompt, examples = service.build_prompt("销售额", 3, datasource_id=8)

    assert examples[0]["suggestion-answer"] == "SELECT 1"
    assert "<sql-examples>" in prompt
    assert "<sql-example>" in prompt
    assert "SELECT 1" in prompt


def test_prompt_requires_datasource_or_assistant_scope():
    service = SQLExampleQueryService(QueryRepository())

    assert service.build_prompt("销售额", 3) == ("", [])
