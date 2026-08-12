"""汇总 Agent Run 中的模型时间旁路观察，不调用模型或修改数据库。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import desc
from sqlmodel import Session, col, select

from apps.chatbi.models import TemporalShadowObservation
from apps.chatbi.models.orm.agent_run import ChatbiAgentRun
from apps.chatbi.services.understanding import (
    summarize_temporal_shadow_observations,
)
from common.core.db import engine


def load_temporal_shadow_observations(
    session: Session,
    *,
    tenant_id: int,
    created_after: datetime,
    limit: int,
) -> tuple[int, list[TemporalShadowObservation]]:
    """读取指定窗口内的 Run，并严格校验其中已有的旁路记录。"""

    runs = session.exec(
        select(ChatbiAgentRun)
        .where(
            ChatbiAgentRun.oid == tenant_id,
            col(ChatbiAgentRun.created_at) >= created_after,
        )
        .order_by(desc(col(ChatbiAgentRun.created_at)))
        .limit(limit)
    ).all()
    observations: list[TemporalShadowObservation] = []
    for run in runs:
        derived_state = run.derived_state
        if not isinstance(derived_state, dict):
            raise RuntimeError(f"TEMPORAL_SHADOW_DERIVED_STATE_INVALID:{run.id}")
        raw_observation = derived_state.get("temporal_shadow_observation")
        if raw_observation is None:
            continue
        try:
            observations.append(
                TemporalShadowObservation.model_validate(raw_observation)
            )
        except ValidationError as exc:
            raise RuntimeError(f"TEMPORAL_SHADOW_OBSERVATION_INVALID:{run.id}") from exc
    return len(runs), observations


def build_temporal_shadow_report(
    *,
    tenant_id: int,
    created_after: datetime,
    scanned_run_count: int,
    observations: list[TemporalShadowObservation],
) -> dict[str, Any]:
    """生成不包含用户问题和模型原文的统计报告。"""

    statistics = summarize_temporal_shadow_observations(observations)
    return {
        "generated_at": datetime.now().isoformat(),
        "tenant_id": tenant_id,
        "created_after": created_after.isoformat(),
        "scanned_run_count": scanned_run_count,
        "statistics": statistics.model_dump(mode="json"),
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总 Agent Run 中的模型时间旁路观察")
    parser.add_argument("--tenant-id", type=_positive_int, required=True)
    parser.add_argument("--since-hours", type=_positive_int, default=168)
    parser.add_argument("--limit", type=_positive_int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    created_after = datetime.now() - timedelta(hours=args.since_hours)
    with Session(engine) as session:
        scanned_run_count, observations = load_temporal_shadow_observations(
            session,
            tenant_id=args.tenant_id,
            created_after=created_after,
            limit=args.limit,
        )
    report = build_temporal_shadow_report(
        tenant_id=args.tenant_id,
        created_after=created_after,
        scanned_run_count=scanned_run_count,
        observations=observations,
    )
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(content, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
