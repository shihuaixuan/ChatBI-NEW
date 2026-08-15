"""从记忆使用记录生成待人工标注的离线评测样本。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, col, select

from apps.memory.models.dto import MemoryEvaluationSample
from apps.memory.models.orm import ChatbiMemoryUsage
from common.core.db import engine


def collect_samples(
    session: Session,
    *,
    oid: int,
    created_after: datetime,
    limit: int,
    user_id: int | None = None,
) -> list[MemoryEvaluationSample]:
    """按 Run 聚合使用记录，生成 expected_memory_ids 为空的待标注样本。"""

    statement = select(ChatbiMemoryUsage.run_id).where(
        col(ChatbiMemoryUsage.oid) == oid,
        col(ChatbiMemoryUsage.created_at) >= created_after,
        col(ChatbiMemoryUsage.run_id).is_not(None),
    )
    if user_id is not None:
        statement = statement.where(col(ChatbiMemoryUsage.user_id) == user_id)
    statement = (
        statement.group_by(ChatbiMemoryUsage.run_id)
        .order_by(func.max(ChatbiMemoryUsage.created_at).desc())
        .limit(limit)
    )
    run_ids = [run_id for run_id in session.exec(statement).all() if run_id]
    if not run_ids:
        return []

    rows_statement = select(ChatbiMemoryUsage).where(
        col(ChatbiMemoryUsage.oid) == oid,
        col(ChatbiMemoryUsage.run_id).in_(run_ids),
    )
    rows = session.exec(rows_statement.order_by(col(ChatbiMemoryUsage.created_at))).all()
    grouped: dict[str, list[ChatbiMemoryUsage]] = defaultdict(list)
    for row in rows:
        if row.run_id is not None:
            grouped[row.run_id].append(row)

    samples: list[MemoryEvaluationSample] = []
    for run_id in run_ids:
        usage_rows = grouped.get(run_id, [])
        variants = {row.recall_variant for row in usage_rows}
        if len(variants) > 1:
            raise RuntimeError(f"MEMORY_RECALL_VARIANT_INCONSISTENT:{run_id}")
        recall_variant = next(iter(variants), None)
        samples.append(
            MemoryEvaluationSample(
                case_id=f"run:{run_id}",
                recall_variant=recall_variant,
                retrieved_memory_ids=list(
                    dict.fromkeys(row.memory_id for row in usage_rows)
                ),
                adopted_memory_ids=list(
                    dict.fromkeys(
                        row.memory_id for row in usage_rows if row.adopted is True
                    )
                ),
                rejected_memory_ids=list(
                    dict.fromkeys(
                        row.memory_id for row in usage_rows if row.adopted is False
                    )
                ),
            )
        )
    return samples


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="生成用户记忆离线评测待标注样本")
    parser.add_argument("--tenant-id", type=_positive_int, required=True)
    parser.add_argument("--user-id", type=_positive_int)
    parser.add_argument("--since-hours", type=_positive_int, default=168)
    parser.add_argument("--limit", type=_positive_int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with Session(engine) as session:
        samples = collect_samples(
            session,
            oid=args.tenant_id,
            user_id=args.user_id,
            created_after=datetime.now() - timedelta(hours=args.since_hours),
            limit=args.limit,
        )
    content = json.dumps(
        {"samples": [sample.model_dump(mode="json") for sample in samples]},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if args.output is None:
        print(content, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
