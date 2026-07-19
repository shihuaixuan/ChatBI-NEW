import logging
from xml.dom.minidom import parseString

import dicttoxml  # type: ignore[import-untyped]

from apps.knowledge.repository import SQLExampleRepository, SQLExampleVectorSearch
from apps.template.generate_chart.generator import get_base_data_training_template


class SQLExampleQueryService:
    """ChatBI 和 Agent 共用的 SQL 示例召回入口。"""

    def __init__(
        self,
        repository: SQLExampleRepository,
        vector_search: SQLExampleVectorSearch | None = None,
    ) -> None:
        self._repository = repository
        self._vector_search = vector_search

    def search(
        self,
        question: str,
        workspace_id: int,
        *,
        datasource_id: int | None = None,
        assistant_id: int | None = None,
    ) -> list[dict[str, str]]:
        normalized_question = question.strip()
        if not normalized_question:
            return []
        example_ids = self._repository.search_lexical_ids(
            workspace_id,
            normalized_question,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
        )
        if self._vector_search is not None:
            example_ids.extend(
                self._vector_search.search_ids(
                    workspace_id,
                    normalized_question,
                    datasource_id=datasource_id,
                    assistant_id=assistant_id,
                )
            )
        unique_ids = list(dict.fromkeys(example_ids))
        return [item.to_legacy_dict() for item in self._repository.get_matches(unique_ids)]

    def build_prompt(
        self,
        question: str,
        workspace_id: int,
        *,
        datasource_id: int | None = None,
        assistant_id: int | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        if datasource_id is None and assistant_id is None:
            return "", []
        examples = self.search(
            question,
            workspace_id or 1,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
        )
        if not examples:
            return "", []
        content = self._to_xml(examples)
        return (
            get_base_data_training_template().format(  # type: ignore[no-untyped-call]
                data_training=content
            ),
            examples,
        )

    @staticmethod
    def _to_xml(examples: list[dict[str, str]]) -> str:
        def item_name(parent: str) -> str:
            return "sql-example" if parent == "sql-examples" else "item"

        dicttoxml.LOG.setLevel(logging.ERROR)
        xml = dicttoxml.dicttoxml(
            examples,
            cdata=["question", "suggestion-answer"],
            custom_root="sql-examples",
            item_func=item_name,
            xml_declaration=False,
            encoding="utf-8",
            attr_type=False,
        ).decode("utf-8")
        pretty_xml = parseString(xml).toprettyxml()
        if pretty_xml.startswith("<?xml"):
            pretty_xml = pretty_xml[pretty_xml.find(">") + 1 :].lstrip()
        for escaped, original in {
            "&lt;": "<",
            "&gt;": ">",
            "&amp;": "&",
            "&quot;": '"',
            "&apos;": "'",
        }.items():
            pretty_xml = pretty_xml.replace(escaped, original)
        return pretty_xml
