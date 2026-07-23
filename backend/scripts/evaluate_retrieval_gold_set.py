"""评估 Graph、Agent 或 shadow 策略生成的统一检索基线。"""

from __future__ import annotations

import argparse
from pathlib import Path

from apps.retrieval.query.evaluation import (
    evaluate_baseline,
    load_baseline,
    load_gold_set,
    report_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="评估统一检索 Gold Set")
    parser.add_argument("--gold-set", type=Path, required=True, help="Gold Set JSON 文件")
    parser.add_argument("--baseline", type=Path, required=True, help="待评估基线 JSON 文件")
    parser.add_argument("--top-k", type=int, default=5, help="召回率计算使用的候选深度")
    parser.add_argument("--output", type=Path, help="可选的报告输出路径")
    args = parser.parse_args()

    report = evaluate_baseline(
        load_gold_set(args.gold_set),
        load_baseline(args.baseline),
        top_k=args.top_k,
    )
    payload = report_json(report)
    if args.output is not None:
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
