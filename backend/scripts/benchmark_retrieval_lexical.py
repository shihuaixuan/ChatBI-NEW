"""输出 PostgreSQL 中文词法方案对比报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlmodel import Session

from apps.retrieval.query.lexical_benchmark import (
    evaluate_lexical_strategies,
    load_lexical_benchmark,
)
from common.core.db import engine


def main() -> None:
    parser = argparse.ArgumentParser(description="评测中文 exact/FTS/pg_trgm 与 RRF")
    parser.add_argument("--gold-set", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    benchmark = load_lexical_benchmark(args.gold_set)
    with Session(engine) as session:
        report = evaluate_lexical_strategies(session, benchmark, top_k=args.top_k)
    rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
