"""基于 PostgreSQL 实际算子的中文词法召回基准。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text


class LexicalCorpusItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    document: str = Field(min_length=1)
    embedding: list[float] = Field(min_length=1)


class LexicalGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    relevant_ids: list[str] = Field(min_length=1)
    query_embedding: list[float] = Field(min_length=1)


class LexicalBenchmarkSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    corpus: list[LexicalCorpusItem] = Field(min_length=1)
    cases: list[LexicalGoldenCase] = Field(min_length=1)


class LexicalBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    top_k: int = Field(gt=0)
    case_count: int = Field(gt=0)
    recall_at_k: dict[str, float]
    selected_lexical_strategy: str
    case_results: dict[str, dict[str, list[str]]]


def load_lexical_benchmark(path: Path) -> LexicalBenchmarkSet:
    """加载可版本化的中文词法 gold set。"""

    return LexicalBenchmarkSet.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_lexical_strategies(
    session: Any,
    benchmark: LexicalBenchmarkSet,
    *,
    top_k: int = 3,
) -> LexicalBenchmarkReport:
    """直接执行 PostgreSQL exact/FTS/pg_trgm，并比较 RRF 后的 Recall@K。"""

    if top_k <= 0:
        raise ValueError("词法 benchmark top_k 必须大于 0")
    corpus_json = json.dumps(
        [item.model_dump(mode="json") for item in benchmark.corpus],
        ensure_ascii=False,
    )
    results: dict[str, dict[str, list[str]]] = {}
    recalls = {
        "exact_alias": 0.0,
        "tsvector_simple": 0.0,
        "pg_trgm": 0.0,
        "dense": 0.0,
        "rrf": 0.0,
    }
    for case in benchmark.cases:
        exact_ids = _query_ids(session, _EXACT_ALIAS_BENCHMARK_SQL, corpus_json, case.query, top_k)
        fts_ids = _query_ids(session, _FTS_BENCHMARK_SQL, corpus_json, case.query, top_k)
        trigram_ids = _query_ids(session, _TRIGRAM_BENCHMARK_SQL, corpus_json, case.query, top_k)
        dense_ids = _query_ids(
            session,
            _DENSE_BENCHMARK_SQL,
            corpus_json,
            case.query,
            top_k,
            extra={"query_vector": str(case.query_embedding)},
        )
        fused_ids = _rrf_ids([exact_ids, fts_ids, trigram_ids, dense_ids], top_k)
        case_result = {
            "exact_alias": exact_ids,
            "tsvector_simple": fts_ids,
            "pg_trgm": trigram_ids,
            "dense": dense_ids,
            "rrf": fused_ids,
        }
        results[case.case_id] = case_result
        relevant = set(case.relevant_ids)
        for strategy, ids in case_result.items():
            recalls[strategy] += len(relevant.intersection(ids)) / len(relevant)

    case_count = len(benchmark.cases)
    recall_at_k = {
        strategy: round(total / case_count, 6)
        for strategy, total in recalls.items()
    }
    selected = (
        "pg_trgm"
        if recall_at_k["pg_trgm"] >= recall_at_k["tsvector_simple"]
        else "tsvector_simple"
    )
    return LexicalBenchmarkReport(
        version=benchmark.version,
        top_k=top_k,
        case_count=case_count,
        recall_at_k=recall_at_k,
        selected_lexical_strategy=selected,
        case_results=results,
    )


def _query_ids(
    session: Any,
    statement: Any,
    corpus_json: str,
    query: str,
    top_k: int,
    *,
    extra: dict[str, Any] | None = None,
) -> list[str]:
    parameters = {"corpus": corpus_json, "query": query, "top_k": top_k}
    parameters.update(extra or {})
    rows = session.execute(
        statement,
        parameters,
    ).all()
    return [str(row[0]) for row in rows]


def _rrf_ids(rankings: list[list[str]], limit: int, rrf_k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, resource_id in enumerate(ranking, start=1):
            scores[resource_id] = scores.get(resource_id, 0.0) + 1.0 / (rrf_k + rank)
    return [
        resource_id
        for resource_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


_CORPUS_CTE = """
WITH corpus AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:corpus AS jsonb))
         AS item(id text, name text, aliases jsonb, document text, embedding jsonb)
)
"""

_EXACT_ALIAS_BENCHMARK_SQL = text(
    _CORPUS_CTE
    + """
SELECT id
FROM corpus
WHERE lower(btrim(name)) = lower(btrim(:query))
   OR EXISTS (
       SELECT 1 FROM jsonb_array_elements_text(aliases) AS alias(value)
       WHERE lower(btrim(alias.value)) = lower(btrim(:query))
   )
ORDER BY id
LIMIT :top_k
"""
)

_FTS_BENCHMARK_SQL = text(
    _CORPUS_CTE
    + """
SELECT id
FROM corpus
WHERE to_tsvector('simple', document) @@ plainto_tsquery('simple', :query)
ORDER BY ts_rank(to_tsvector('simple', document), plainto_tsquery('simple', :query)) DESC, id
LIMIT :top_k
"""
)

_TRIGRAM_BENCHMARK_SQL = text(
    _CORPUS_CTE
    + """
SELECT id
FROM corpus
WHERE GREATEST(similarity(name, :query), similarity(document, :query)) > 0
ORDER BY GREATEST(similarity(name, :query), similarity(document, :query)) DESC, id
LIMIT :top_k
"""
)

_DENSE_BENCHMARK_SQL = text(
    _CORPUS_CTE
    + """
SELECT id
FROM corpus
ORDER BY ((embedding::text)::vector <=> :query_vector), id
LIMIT :top_k
"""
)


__all__ = [
    "LexicalBenchmarkReport",
    "LexicalBenchmarkSet",
    "evaluate_lexical_strategies",
    "load_lexical_benchmark",
]
