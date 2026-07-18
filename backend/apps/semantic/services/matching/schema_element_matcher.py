from __future__ import annotations

from dataclasses import dataclass

from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SchemaElementMatch,
    SchemaMapInfo,
)
from apps.semantic.utils.semantic_aliases import value_aliases
from apps.semantic.utils.text import unique_texts


@dataclass
class SchemaMatchWord:
    text: str
    element: SchemaElement
    word: str
    similarity: float


class SchemaVocabularyBuilder:
    """根据语义资产名称和别名构建匹配词表。"""

    def build_words(self, schema: DatasetSchema) -> list[SchemaMatchWord]:
        words: list[SchemaMatchWord] = []
        elements = [
            *schema.metrics,
            *schema.dimensions,
            *schema.dimension_values,
            *schema.terms,
        ]
        for element in elements:
            words.extend(self._element_words(element))
        words.sort(
            key=lambda item: (-len(item.text), item.element.type, item.element.id)
        )
        return words

    def _element_words(self, element: SchemaElement) -> list[SchemaMatchWord]:
        raw_words = unique_texts(
            [element.name, element.biz_name, *(element.alias or [])]
        )
        if element.type == "VALUE":
            raw_words = unique_texts(element.alias or [])
        return [
            SchemaMatchWord(
                text=word,
                element=element,
                word=_match_word(element, word),
                similarity=1.0,
            )
            for word in raw_words
        ]


class SchemaElementMatcher:
    """匹配用户问题中出现的语义元素。"""

    def __init__(
        self,
        vocabulary_builder: SchemaVocabularyBuilder | None = None,
    ):
        self.vocabulary_builder = (
            vocabulary_builder or SchemaVocabularyBuilder()
        )

    def match(self, query_text: str, schema: DatasetSchema) -> SchemaMapInfo:
        matches: list[SchemaElementMatch] = []
        occupied: list[range] = []
        for word in self.vocabulary_builder.build_words(schema):
            offset = query_text.find(word.text)
            if offset < 0:
                continue
            span = range(offset, offset + len(word.text))
            if _overlaps(span, occupied):
                continue
            occupied.append(span)
            matches.append(
                SchemaElementMatch(
                    element=word.element,
                    offset=offset,
                    similarity=word.similarity,
                    detect_word=word.text,
                    word=word.word,
                    frequency=0,
                )
            )
        matches.sort(
            key=lambda item: (
                _type_order(item.element.type),
                item.offset,
                -len(item.detect_word),
            )
        )
        return SchemaMapInfo(data_set_element_matches={schema.data_set.id: matches})


def _match_word(element: SchemaElement, text: str) -> str:
    if element.type != "VALUE":
        return element.name
    for value_map in element.schema_value_maps:
        if text in value_aliases(value_map) or text == str(
            value_map.get("value") or ""
        ):
            return str(
                value_map.get("value")
                or value_map.get("techName")
                or value_map.get("tech_name")
                or text
            )
    return text


def _overlaps(span: range, occupied: list[range]) -> bool:
    return any(
        span.start < item.stop and item.start < span.stop for item in occupied
    )


def _type_order(element_type: str) -> int:
    return {"METRIC": 0, "DIMENSION": 1, "VALUE": 2, "TERM": 3}.get(
        element_type, 9
    )
