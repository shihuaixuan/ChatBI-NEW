"""新旧引擎评测结果双跑比较（doc38 §11.3.2 比较器的评测入口）。

读取 ``run_research_agent_eval.py`` 产出的两份结果文件（legacy 与
agent 引擎各一份），按 case_id 配对，从数据库取两侧 run 行的
``derived_state``，交给 :func:`compare_dual_runs` 做四组维度比较，
输出逐 case 报告与聚合分布。只读数据库，不调用模型、不修改数据。

示例::

    python scripts/compare_research_agent_eval.py \
        --legacy data/research_agent_eval_legacy_full_20260823.json \
        --agent data/research_agent_eval_agent_full_20260823.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.chatbi.services.research.comparison import (  # noqa: E402
    VERDICT_DIFFERENT,
    VERDICT_EQUAL,
    VERDICT_NOT_COMPARABLE,
    compare_dual_runs,
)
from common.core.db import engine  # noqa: E402


def _load_records(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        record["case_id"]: record
        for record in payload.get("records", [])
        if isinstance(record, dict) and record.get("case_id")
    }


def _load_derived_state(session: Session, run_id: int) -> dict[str, Any]:
    row = session.exec(
        text(
            "SELECT r.derived_state FROM chatbi_agent_run r "
            "WHERE r.id = :run_id"
        ).bindparams(run_id=run_id)
    ).mappings().one_or_none()
    if row is None:
        raise SystemExit(f"run 不存在: {run_id}")
    derived = row["derived_state"]
    return derived if isinstance(derived, dict) else {}


def compare_case(
    session: Session,
    *,
    legacy_record: dict[str, Any],
    agent_record: dict[str, Any],
) -> dict[str, Any]:
    legacy_run_id = (legacy_record.get("run") or {}).get("id")
    agent_run_id = (agent_record.get("run") or {}).get("id")
    comparison = compare_dual_runs(
        _load_derived_state(session, int(legacy_run_id)),
        _load_derived_state(session, int(agent_run_id)),
    )
    verdict_counts = Counter(item.verdict for item in comparison.dimensions)
    differing = [
        {
            "group": item.group,
            "dimension": item.dimension,
            "legacy_value": item.legacy_value,
            "agent_value": item.agent_value,
            "detail": item.detail,
        }
        for item in comparison.dimensions
        if item.verdict == VERDICT_DIFFERENT
    ]
    not_comparable = [
        f"{item.group}.{item.dimension}" for item in comparison.dimensions if item.verdict == VERDICT_NOT_COMPARABLE
    ]
    return {
        "case_id": legacy_record.get("case_id"),
        "name": legacy_record.get("name"),
        "question": legacy_record.get("question"),
        "outcome": {
            "legacy": legacy_record.get("outcome"),
            "agent": agent_record.get("outcome"),
        },
        "run_ids": {"legacy": legacy_run_id, "agent": agent_run_id},
        "has_different": comparison.has_different(),
        "verdict_counts": dict(verdict_counts),
        "differing_dimensions": differing,
        "not_comparable_dimensions": not_comparable,
        "facts": comparison.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"data/research_agent_eval_comparison_{date.today():%Y%m%d}.json"),
    )
    args = parser.parse_args()

    legacy_records = _load_records(args.legacy)
    agent_records = _load_records(args.agent)
    shared_ids = sorted(set(legacy_records) & set(agent_records))
    only_legacy = sorted(set(legacy_records) - set(agent_records))
    only_agent = sorted(set(agent_records) - set(legacy_records))

    entries: list[dict[str, Any]] = []
    dimension_verdicts: Counter[str] = Counter()
    with Session(engine) as session:
        for case_id in shared_ids:
            entry = compare_case(
                session,
                legacy_record=legacy_records[case_id],
                agent_record=agent_records[case_id],
            )
            entries.append(entry)

    for entry in entries:
        for diff in entry["differing_dimensions"]:
            dimension_verdicts[f"{diff['group']}.{diff['dimension']}"] += 1

    equal_cases = sum(1 for entry in entries if not entry["has_different"])
    summary = {
        "generated_at": f"{date.today():%Y-%m-%d}",
        "legacy_file": str(args.legacy),
        "agent_file": str(args.agent),
        "cases_compared": len(entries),
        "cases_all_equal": equal_cases,
        "cases_with_differences": len(entries) - equal_cases,
        "only_in_legacy": only_legacy,
        "only_in_agent": only_agent,
        "differing_dimension_distribution": dict(
            dimension_verdicts.most_common()
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "comparisons": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 72)
    print(
        f"双跑比较: {summary['cases_compared']} 例 | "
        f"全维度一致 {equal_cases} | 存在差异 {summary['cases_with_differences']}"
    )
    if only_legacy or only_agent:
        print(f"  仅一侧有记录: legacy={only_legacy} agent={only_agent}")
    for name, count in dimension_verdicts.most_common():
        print(f"  [different] {name}: {count}")
    print(f"明细已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
