"""采集当前 Graph/Agent Headless 检索基线，不调用问题理解模型。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from sqlmodel import Session

from apps.capabilities.semantic.retrieval import retrieve_semantic_assets
from apps.retrieval.evaluation import (
    RecordedRetrievalResult,
    RetrievalBaseline,
    RetrievalGoldenCase,
    load_gold_set,
)
from apps.retrieval.payload import semantic_payload_to_bundle
from apps.retrieval.schemas import RetrievalChannel, RetrievalChannelStatus
from apps.retrieval.service import build_retrieval_service
from common.core.db import engine


def _graph_result(session: Session, case: RetrievalGoldenCase) -> RecordedRetrievalResult:
    result = build_retrieval_service(session).retrieve(case.request)
    return RecordedRetrievalResult(case_id=case.case_id, bundle=result.bundle)


def _agent_result(session: Session, case: RetrievalGoldenCase) -> RecordedRetrievalResult:
    service = build_retrieval_service(session)
    started = time.perf_counter()
    raw = retrieve_semantic_assets(
        session,
        oid=case.request.tenant_id,
        dataset_id=case.request.scope.dataset_ids[0],
        question=case.request.rewritten_question,
        intent=case.request.intent.model_dump(mode="json"),
        actor_id=case.request.actor_id,
        request_id=case.request.request_id,
        retrieval_service=service,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    dense = _channel_diagnostic(raw, RetrievalChannel.DENSE)
    bundle = semantic_payload_to_bundle(
        case.request,
        raw,
        dense_status=RetrievalChannelStatus(dense.get("status") or RetrievalChannelStatus.SKIPPED),
        dense_error_code=dense.get("error_code"),
        dense_latency_ms=float(dense.get("latency_ms") or 0),
        latency_ms=latency_ms,
    )
    return RecordedRetrievalResult(case_id=case.case_id, bundle=bundle)


def _channel_diagnostic(raw: dict[str, Any], channel: RetrievalChannel) -> dict[str, Any]:
    diagnostics = raw.get("retrieval_diagnostics") or {}
    for item in diagnostics.get("channels") or []:
        if isinstance(item, dict) and item.get("channel") == channel.value:
            return cast(dict[str, Any], item)
    return {"status": RetrievalChannelStatus.SKIPPED.value}


def _write_baseline(path: Path, baseline: RetrievalBaseline) -> None:
    path.write_text(
        json.dumps(baseline.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="采集当前 Graph/Agent 检索基线")
    parser.add_argument("--gold-set", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cases = load_gold_set(args.gold_set)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(timezone.utc)
    with Session(engine) as session:
        graph_results = [_graph_result(session, case) for case in cases]
        agent_results = [_agent_result(session, case) for case in cases]

    _write_baseline(
        args.output_dir / "graph_baseline.json",
        RetrievalBaseline(
            implementation="graph",
            captured_at=captured_at,
            strategy_version="semantic-binding",
            results=graph_results,
        ),
    )
    _write_baseline(
        args.output_dir / "agent_baseline.json",
        RetrievalBaseline(
            implementation="agent",
            captured_at=captured_at,
            strategy_version="semantic-binding",
            results=agent_results,
        ),
    )


if __name__ == "__main__":
    main()
