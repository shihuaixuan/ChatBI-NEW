"""只读统计 Agent Run 的时间解释来源，不输出用户问题或时间原文。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, col, select

from apps.chatbi.models.orm.agent_run import ChatbiAgentRun
from common.core.db import engine

_KNOWN_SOURCES = {"legacy_rule", "model", "user_confirmation"}


def load_temporal_source_counts(
    session: Session,
    *,
    tenant_id: int,
    created_after: datetime,
    limit: int,
) -> tuple[int, dict[str, int]]:
    """读取问题理解快照并统计来源；旧快照缺少来源时单独计数。"""

    runs = session.exec(
        select(ChatbiAgentRun)
        .where(
            ChatbiAgentRun.oid == tenant_id,
            col(ChatbiAgentRun.created_at) >= created_after,
        )
        .order_by(desc(col(ChatbiAgentRun.created_at)))
        .limit(limit)
    ).all()
    counts: Counter[str] = Counter()
    for run in runs:
        derived_state = run.derived_state
        if not isinstance(derived_state, dict):
            raise RuntimeError(f"TEMPORAL_SOURCE_DERIVED_STATE_INVALID:{run.id}")
        understanding = derived_state.get("question_understanding")
        if not isinstance(understanding, dict):
            counts["snapshot_missing"] += 1
            continue
        intent = understanding.get("intent")
        time_range = intent.get("time_range") if isinstance(intent, dict) else None
        if not isinstance(time_range, dict):
            counts["snapshot_invalid"] += 1
            continue
        if str(time_range.get("value_status") or "").lower() != "provided":
            counts["no_time"] += 1
            continue
        raw_source = time_range.get("interpretation_source")
        if raw_source in (None, ""):
            counts["unrecorded"] += 1
            continue
        source = str(raw_source)
        counts[source if source in _KNOWN_SOURCES else "invalid_source"] += 1
    return len(runs), dict(counts)


def build_temporal_source_report(
    *,
    tenant_id: int,
    created_after: datetime,
    scanned_run_count: int,
    source_counts: dict[str, int],
) -> dict[str, Any]:
    """生成旧规则使用率报告；分母只包含明确提供时间的 Run。"""

    interpreted_count = sum(
        source_counts.get(source, 0)
        for source in (*sorted(_KNOWN_SOURCES), "unrecorded", "invalid_source")
    )
    legacy_count = source_counts.get("legacy_rule", 0)
    return {
        "generated_at": datetime.now().isoformat(),
        "tenant_id": tenant_id,
        "created_after": created_after.isoformat(),
        "scanned_run_count": scanned_run_count,
        "interpreted_time_range_count": interpreted_count,
        "source_counts": source_counts,
        "legacy_rule_rate": (
            legacy_count / interpreted_count if interpreted_count else None
        ),
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="统计 Agent Run 的时间解释来源")
    parser.add_argument("--tenant-id", type=_positive_int, required=True)
    parser.add_argument("--since-hours", type=_positive_int, default=168)
    parser.add_argument("--limit", type=_positive_int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    created_after = datetime.now() - timedelta(hours=args.since_hours)
    with Session(engine) as session:
        scanned_run_count, source_counts = load_temporal_source_counts(
            session,
            tenant_id=args.tenant_id,
            created_after=created_after,
            limit=args.limit,
        )
    report = build_temporal_source_report(
        tenant_id=args.tenant_id,
        created_after=created_after,
        scanned_run_count=scanned_run_count,
        source_counts=source_counts,
    )
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(content, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
