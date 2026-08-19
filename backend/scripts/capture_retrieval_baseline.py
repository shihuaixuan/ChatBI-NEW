"""采集当前 Graph/Agent 候选资产检索基线，不调用候选绑定模型。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlmodel import Session

from apps.retrieval.query.evaluation import (
    RetrievalGoldenCase,
    load_gold_set,
)
from apps.retrieval.query.service import build_retrieval_service
from common.core.db import engine


def _candidate_result(session: Session, case: RetrievalGoldenCase) -> dict[str, Any]:
    """记录候选分组和召回诊断，不把候选伪装成最终绑定结果。"""

    result = build_retrieval_service(session).retrieve(case.request)
    return {
        "case_id": case.case_id,
        "request_id": case.request.request_id,
        "payload": result.payload,
        "filters": result.filters,
    }


def _write_baseline(path: Path, implementation: str, results: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            {
                "implementation": implementation,
                "stage": "candidate_retrieval",
                "strategy_version": "semantic-binding",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="采集当前 Graph/Agent 检索基线")
    parser.add_argument("--gold-set", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cases = load_gold_set(args.gold_set)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        results = [_candidate_result(session, case) for case in cases]

    _write_baseline(args.output_dir / "candidate_retrieval.json", "shared", results)


if __name__ == "__main__":
    main()
