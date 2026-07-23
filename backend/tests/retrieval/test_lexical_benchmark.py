"""中文词法方案的 PostgreSQL 可重复基准。"""

from pathlib import Path

from sqlmodel import Session

from apps.retrieval.query.lexical_benchmark import (
    LexicalBenchmarkReport,
    evaluate_lexical_strategies,
    load_lexical_benchmark,
)
from common.core.db import engine

GOLD_SET = Path(__file__).parent / "golden" / "chinese_lexical.json"
BASELINE = Path(__file__).parent / "baselines" / "chinese_lexical_report.json"


def test_pg_trgm_outperforms_simple_fts_and_rrf_is_not_worse_than_best_channel():
    benchmark = load_lexical_benchmark(GOLD_SET)
    with Session(engine) as session:
        report = evaluate_lexical_strategies(session, benchmark, top_k=3)

    assert report.selected_lexical_strategy == "pg_trgm"
    assert report.recall_at_k["pg_trgm"] > report.recall_at_k["tsvector_simple"]
    assert report.recall_at_k["rrf"] >= max(
        report.recall_at_k["exact_alias"],
        report.recall_at_k["tsvector_simple"],
        report.recall_at_k["pg_trgm"],
        report.recall_at_k["dense"],
    )
    assert report == LexicalBenchmarkReport.model_validate_json(BASELINE.read_text(encoding="utf-8"))
