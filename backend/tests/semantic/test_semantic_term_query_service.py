from types import SimpleNamespace

import pytest

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.term_query_service import SemanticTermQueryService


def _schema(*terms: SchemaElement) -> DatasetSchema:
    return DatasetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营数据集",
            id=20,
            name="经营数据集",
            biz_name="business_dataset",
            type="DATASET",
        ),
        terms=list(terms),
    )


def _term(term_id: int, name: str, aliases: list[str]) -> SchemaElement:
    return SchemaElement(
        data_set_id=20,
        data_set_name="经营数据集",
        id=term_id,
        name=name,
        biz_name=name,
        type="TERM",
        alias=aliases,
        description=f"{name}定义",
    )


def test_search_prefers_exact_match_and_returns_stable_contract():
    reader = SimpleNamespace(
        build_dataset_schema=lambda _oid, _dataset_id: _schema(
            _term(1, "销售额", ["GMV"]),
            _term(2, "GMV目标", []),
        )
    )

    results = SemanticTermQueryService(reader).search(1, 20, "GMV")

    assert [item.term_id for item in results] == [1, 2]
    assert results[0].model_dump() == {
        "term_id": 1,
        "dataset_id": 20,
        "words": ["销售额", "GMV"],
        "description": "销售额定义",
        "related_assets": [],
    }


def test_search_matches_term_contained_in_question():
    reader = SimpleNamespace(
        build_dataset_schema=lambda _oid, _dataset_id: _schema(
            _term(1, "客单价", ["ATV"]),
        )
    )

    results = SemanticTermQueryService(reader).search(
        1,
        20,
        "本月客单价是多少",
    )

    assert [item.words for item in results] == [["客单价", "ATV"]]


def test_search_rejects_invalid_limit():
    reader = SimpleNamespace(build_dataset_schema=lambda _oid, _dataset_id: _schema())

    with pytest.raises(SemanticValidationError) as exc_info:
        SemanticTermQueryService(reader).search(1, 20, "销售额", limit=0)

    assert exc_info.value.detail == "SEMANTIC_TERM_SEARCH_LIMIT_INVALID"
