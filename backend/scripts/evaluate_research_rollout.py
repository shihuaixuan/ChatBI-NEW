"""切流就绪判定入口（doc38 §11.3.3–§11.3.5 的运营 CLI）。

把 :mod:`apps.chatbi.services.research.readiness` 接到数据源上，产出
promote/blocked 结论与逐项明细。只读数据库与配置文件，不调模型、
不修改任何状态。

样本来源二选一：

- 默认：扫描库内 shadow 双跑行（``derived_state.shadow`` 标记），
  与其父 run 组成 pair——生产流量积累的真实双跑样本；
- ``--eval-legacy/--eval-agent``：读取 ``run_research_agent_eval.py``
  产出的两份结果文件按 case 配对——阶段 0/7.5 评测样本的基线提案模式。

示例::

    python scripts/evaluate_research_rollout.py \\
        --config config/research_eval_thresholds.json --limit 50

    python scripts/evaluate_research_rollout.py \\
        --eval-legacy data/research_agent_eval_legacy_full_20260823.json \\
        --eval-agent data/research_agent_eval_agent_rescored_20260823.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

from apps.chatbi.services.research.readiness import (  # noqa: E402
    DualPair,
    build_readiness_report,
    load_quality_thresholds,
    load_runtime_limits,
)
from common.core.config import settings  # noqa: E402
from common.core.db import engine  # noqa: E402


def _derived_by_id(session: Session, run_id: int) -> dict[str, Any]:
    row = session.exec(
        text("SELECT derived_state FROM chatbi_agent_run WHERE id = :run_id").bindparams(
            run_id=run_id
        )
    ).mappings().one_or_none()
    if row is None:
        raise SystemExit(f"run 不存在: {run_id}")
    derived = row["derived_state"]
    return derived if isinstance(derived, dict) else {}


def _collect_shadow_pairs(
    session: Session,
    *,
    limit: int,
    tenant_id: int | None,
) -> list[DualPair]:
    """扫描 shadow 双跑行并与其父 run 组成 pair（新→旧顺序取行）。"""

    conditions = "derived_state ? 'shadow' AND execution_mode = 'research'"
    params: dict[str, Any] = {"limit": limit}
    if tenant_id is not None:
        conditions += " AND oid = :oid"
        params["oid"] = tenant_id
    rows = session.exec(
        text(
            f"SELECT id, oid, derived_state FROM chatbi_agent_run "
            f"WHERE {conditions} ORDER BY id DESC LIMIT :limit"
        ).bindparams(**params)
    ).mappings().all()
    pairs: list[DualPair] = []
    for row in rows:
        derived = row["derived_state"]
        if not isinstance(derived, dict):
            continue
        marker = derived.get("shadow") or {}
        parent_id = marker.get("parent_run_id")
        if not isinstance(parent_id, int):
            continue
        pairs.append(
            DualPair(
                parent_run_id=parent_id,
                agent_run_id=int(row["id"]),
                legacy_derived=_derived_by_id(session, parent_id),
                agent_derived=derived,
                source="shadow",
            )
        )
    return pairs


def _collect_eval_pairs(
    session: Session,
    *,
    legacy_file: Path,
    agent_file: Path,
) -> list[DualPair]:
    """按 case_id 配对两份评测结果文件，从库取两侧派生状态。"""

    def _records(path: Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            record["case_id"]: record
            for record in payload.get("records", [])
            if isinstance(record, dict) and record.get("case_id")
        }

    legacy_records = _records(legacy_file)
    agent_records = _records(agent_file)
    pairs: list[DualPair] = []
    for case_id in sorted(set(legacy_records) & set(agent_records)):
        legacy_run_id = (legacy_records[case_id].get("run") or {}).get("id")
        agent_run_id = (agent_records[case_id].get("run") or {}).get("id")
        if not legacy_run_id or not agent_run_id:
            continue
        pairs.append(
            DualPair(
                parent_run_id=int(legacy_run_id),
                agent_run_id=int(agent_run_id),
                legacy_derived=_derived_by_id(session, int(legacy_run_id)),
                agent_derived=_derived_by_id(session, int(agent_run_id)),
                source="eval",
            )
        )
    return pairs


def _step_latency_ms(session: Session, run_id: int) -> int | None:
    value = session.exec(
        text("SELECT COALESCE(SUM(latency_ms), 0) FROM chatbi_agent_step WHERE run_id = :run_id").bindparams(
            run_id=run_id
        )
    ).scalar()
    return int(value) if value else None


def _timed_out(session: Session, run_id: int) -> bool:
    error = session.exec(
        text("SELECT error FROM chatbi_agent_run WHERE id = :run_id").bindparams(run_id=run_id)
    ).scalar()
    return bool(error and "TIMEOUT" in str(error).upper())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(settings.CHATBI_RESEARCH_EVAL_CONFIG or "") or None,
        help="评测配置 JSON 路径；缺省读 CHATBI_RESEARCH_EVAL_CONFIG，为空即未配置",
    )
    parser.add_argument("--limit", type=int, default=50, help="shadow 模式最多取多少对")
    parser.add_argument("--tenant-id", type=int, default=None)
    parser.add_argument("--eval-legacy", type=Path, default=None)
    parser.add_argument("--eval-agent", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"data/research_rollout_readiness_{date.today():%Y%m%d}.json"),
    )
    args = parser.parse_args()

    if bool(args.eval_legacy) != bool(args.eval_agent):
        raise SystemExit("--eval-legacy 与 --eval-agent 必须同时提供")

    thresholds = load_quality_thresholds(args.config)
    runtime_limits = load_runtime_limits(args.config)

    with Session(engine) as session:
        if args.eval_legacy:
            pairs = _collect_eval_pairs(
                session, legacy_file=args.eval_legacy, agent_file=args.eval_agent
            )
        else:
            pairs = _collect_shadow_pairs(session, limit=args.limit, tenant_id=args.tenant_id)

        report, diagnostics = build_readiness_report(
            pairs,
            thresholds=thresholds,
            runtime_limits=runtime_limits,
            latency_lookup=lambda run_id: _step_latency_ms(session, run_id),
            timeout_lookup=lambda run_id: _timed_out(session, run_id),
        )

    payload = {
        "generated_at": f"{date.today():%Y-%m-%d}",
        "config": args.config,
        "sample_source": "eval_files" if args.eval_legacy else "shadow_rows",
        **diagnostics,
        "report": report.as_dict(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"切流就绪判定: {payload['decision'].upper()} | 样本 {diagnostics['total_pairs']} 对"
          f"（计入 {diagnostics['evaluated_pairs']}）| 来源 {payload['sample_source']}")
    print(f"质量指标: {json.dumps(diagnostics.get('quality_metrics') or {}, ensure_ascii=False)}")
    if report.blockers:
        print("阻断项:")
        for blocker in report.blockers[:20]:
            print(f"  - {blocker}")
    print(f"明细已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
