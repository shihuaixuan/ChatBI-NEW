"""只读回放历史 Agent Run，并聚合模型时间计划与旧时间结果的差异。"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import desc
from sqlmodel import Session, col, select

from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.errors import TemporalInterpretationError
from apps.chatbi.models import (
    QuestionUnderstandingOutput,
    TemporalShadowObservation,
    TimeRange,
)
from apps.chatbi.models.orm.agent_run import ChatbiAgentRun
from apps.chatbi.services.understanding import (
    TemporalInterpretationService,
    compare_temporal_shadow,
    summarize_temporal_shadow_observations,
)
from apps.temporal import TemporalContext
from common.core.db import engine


@dataclass(frozen=True, slots=True)
class TemporalReplayCandidate:
    """从历史 Run 恢复出的最小时间理解输入。"""

    rewritten_question: str
    metric_mentions: tuple[str, ...]
    time_mentions: tuple[str, ...]
    legacy_time_range: TimeRange
    temporal_context: TemporalContext


@dataclass(frozen=True, slots=True)
class TemporalReplayLoadResult:
    """历史 Run 筛选结果，异常与重复都显式计数。"""

    scanned_run_count: int
    invalid_snapshot_count: int
    duplicate_count: int
    candidates: tuple[TemporalReplayCandidate, ...]


@dataclass(frozen=True, slots=True)
class TemporalReplayTrialResult:
    """同一批候选的多次回放结果。"""

    observations: tuple[tuple[TemporalShadowObservation, ...], ...]
    usage_metadata: dict[str, int]


def load_temporal_replay_candidates(
    session: Session,
    *,
    tenant_id: int,
    created_after: datetime,
    run_limit: int,
    sample_size: int,
) -> TemporalReplayLoadResult:
    """按最新 Run 读取并按重写问题去重，不修改任何持久化状态。"""

    if tenant_id <= 0:
        raise ValueError("TEMPORAL_REPLAY_TENANT_ID_INVALID")
    if run_limit <= 0:
        raise ValueError("TEMPORAL_REPLAY_RUN_LIMIT_INVALID")
    if sample_size <= 0:
        raise ValueError("TEMPORAL_REPLAY_SAMPLE_SIZE_INVALID")

    runs = session.exec(
        select(ChatbiAgentRun)
        .where(
            ChatbiAgentRun.oid == tenant_id,
            col(ChatbiAgentRun.created_at) >= created_after,
        )
        .order_by(desc(col(ChatbiAgentRun.created_at)))
        .limit(run_limit)
    ).all()
    candidates: list[TemporalReplayCandidate] = []
    seen_questions: set[str] = set()
    invalid_snapshot_count = 0
    duplicate_count = 0

    for run in runs:
        derived_state = run.derived_state
        if not isinstance(derived_state, dict):
            invalid_snapshot_count += 1
            continue
        raw_understanding = derived_state.get("question_understanding")
        if raw_understanding is None:
            continue
        try:
            understanding = QuestionUnderstandingOutput.model_validate(
                raw_understanding
            )
            temporal_context = TemporalContext.model_validate(run.temporal_context)
        except ValidationError:
            invalid_snapshot_count += 1
            continue

        rewritten_question = understanding.rewritten_question
        if rewritten_question in seen_questions:
            duplicate_count += 1
            continue
        seen_questions.add(rewritten_question)
        if len(candidates) < sample_size:
            candidates.append(
                TemporalReplayCandidate(
                    rewritten_question=rewritten_question,
                    metric_mentions=tuple(understanding.intent.metric_mentions),
                    time_mentions=tuple(understanding.intent.time_mentions),
                    legacy_time_range=understanding.intent.time_range,
                    temporal_context=temporal_context,
                )
            )

    return TemporalReplayLoadResult(
        scanned_run_count=len(runs),
        invalid_snapshot_count=invalid_snapshot_count,
        duplicate_count=duplicate_count,
        candidates=tuple(candidates),
    )


def replay_temporal_candidates(
    candidates: tuple[TemporalReplayCandidate, ...],
    service: TemporalInterpretationService,
) -> tuple[list[TemporalShadowObservation], dict[str, int]]:
    """逐条执行时间模型；单条模型失败转为可统计观察，不中断整批回放。"""

    observations: list[TemporalShadowObservation] = []
    usage_items: list[dict[str, int]] = []
    for candidate in candidates:
        try:
            outcome = service.interpret(
                rewritten_question=candidate.rewritten_question,
                metric_mentions=list(candidate.metric_mentions),
                time_mentions=list(candidate.time_mentions),
                temporal_context=candidate.temporal_context,
            )
        except TemporalInterpretationError as exc:
            observations.append(
                TemporalShadowObservation(
                    status="model_error",
                    legacy_time_range=candidate.legacy_time_range,
                    error_code=exc.code,
                )
            )
            usage_items.append(exc.usage_metadata)
            continue
        observations.append(
            compare_temporal_shadow(
                plan=outcome.plan,
                legacy_time_range=candidate.legacy_time_range,
                temporal_context=candidate.temporal_context,
            )
        )
        usage_items.append(outcome.usage_metadata)
    return observations, _merge_usage(*usage_items)


def replay_temporal_trials(
    candidates: tuple[TemporalReplayCandidate, ...],
    service: TemporalInterpretationService,
    *,
    trial_count: int,
) -> TemporalReplayTrialResult:
    """对同一批候选重复回放，保留每次结果用于稳定性比较。"""

    if trial_count < 2:
        raise ValueError("TEMPORAL_REPLAY_TRIAL_COUNT_TOO_SMALL")
    if trial_count > 10:
        raise ValueError("TEMPORAL_REPLAY_TRIAL_COUNT_TOO_LARGE")

    trial_observations: list[tuple[TemporalShadowObservation, ...]] = []
    usage_items: list[dict[str, int]] = []
    for _ in range(trial_count):
        observations, usage_metadata = replay_temporal_candidates(
            candidates,
            service,
        )
        trial_observations.append(tuple(observations))
        usage_items.append(usage_metadata)
    return TemporalReplayTrialResult(
        observations=tuple(trial_observations),
        usage_metadata=_merge_usage(*usage_items),
    )


def summarize_temporal_replay_stability(
    trial_observations: tuple[tuple[TemporalShadowObservation, ...], ...],
) -> dict[str, Any]:
    """按候选比较多次业务结果，不输出问题或单条计划。"""

    if len(trial_observations) < 2:
        raise ValueError("TEMPORAL_REPLAY_TRIAL_COUNT_TOO_SMALL")
    candidate_count = len(trial_observations[0])
    if any(len(items) != candidate_count for items in trial_observations):
        raise ValueError("TEMPORAL_REPLAY_TRIAL_CANDIDATES_MISMATCH")

    stable_candidate_count = 0
    stable_matched_candidate_count = 0
    sequence_counts: Counter[str] = Counter()
    unstable_reason_counts: Counter[str] = Counter()
    for candidate_items in zip(*trial_observations, strict=True):
        fingerprints = tuple(
            _observation_fingerprint(observation) for observation in candidate_items
        )
        statuses = tuple(observation.status for observation in candidate_items)
        sequence_counts[" -> ".join(statuses)] += 1
        if len(set(fingerprints)) != 1:
            component_items = tuple(
                _observation_fingerprint_components(observation)
                for observation in candidate_items
            )
            for component_name in component_items[0]:
                if (
                    len({components[component_name] for components in component_items})
                    > 1
                ):
                    unstable_reason_counts[component_name] += 1
            continue
        stable_candidate_count += 1
        if statuses[0] == "matched":
            stable_matched_candidate_count += 1

    unstable_candidate_count = candidate_count - stable_candidate_count
    return {
        "trial_count": len(trial_observations),
        "candidate_count": candidate_count,
        "stable_candidate_count": stable_candidate_count,
        "stable_matched_candidate_count": stable_matched_candidate_count,
        "stable_non_matched_candidate_count": (
            stable_candidate_count - stable_matched_candidate_count
        ),
        "unstable_candidate_count": unstable_candidate_count,
        "stability_rate": (
            stable_candidate_count / candidate_count if candidate_count else None
        ),
        "status_sequence_counts": dict(sequence_counts),
        "unstable_reason_counts": dict(unstable_reason_counts),
    }


def build_temporal_replay_report(
    *,
    tenant_id: int,
    created_after: datetime,
    load_result: TemporalReplayLoadResult,
    trial_result: TemporalReplayTrialResult,
) -> dict[str, Any]:
    """生成不含问题、计划和原始时间表达的回放报告。"""

    all_observations = [
        observation
        for observations in trial_result.observations
        for observation in observations
    ]
    statistics = summarize_temporal_shadow_observations(all_observations)
    return {
        "generated_at": datetime.now().isoformat(),
        "tenant_id": tenant_id,
        "created_after": created_after.isoformat(),
        "scanned_run_count": load_result.scanned_run_count,
        "invalid_snapshot_count": load_result.invalid_snapshot_count,
        "duplicate_count": load_result.duplicate_count,
        "candidate_count": len(load_result.candidates),
        "usage_metadata": trial_result.usage_metadata,
        "statistics": statistics.model_dump(mode="json"),
        "trial_statistics": [
            summarize_temporal_shadow_observations(observations).model_dump(mode="json")
            for observations in trial_result.observations
        ],
        "stability": summarize_temporal_replay_stability(trial_result.observations),
    }


def _observation_fingerprint(observation: TemporalShadowObservation) -> bytes:
    """生成仅在内存中使用的时间语义结果指纹。"""

    return json.dumps(
        _observation_fingerprint_components(observation),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _observation_fingerprint_components(
    observation: TemporalShadowObservation,
) -> dict[str, str]:
    """拆分可比较的时间语义组成，供聚合不稳定原因。"""

    payload = observation.model_dump(mode="json", exclude={"legacy_time_range"})
    plan = payload.get("plan")
    if isinstance(plan, dict):
        # 置信度不参与执行语义，不能仅因分数波动判定时间计划不稳定。
        plan.pop("confidence", None)
    return {
        key: json.dumps(
            payload.get(key),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for key in (
            "status",
            "plan",
            "resolved_plan",
            "difference_codes",
            "error_code",
        )
    }


def _merge_usage(*items: dict[str, int]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "total_tokens")
    return {key: sum(int(item.get(key) or 0) for item in items) for key in keys}


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return parsed


def _trial_count(value: str) -> int:
    parsed = _positive_int(value)
    if parsed < 2 or parsed > 10:
        raise argparse.ArgumentTypeError("必须是 2 到 10 之间的整数")
    return parsed


def _configure_replay_logging() -> None:
    """禁止模型客户端调试日志输出历史问题和完整请求正文。"""

    for logger_name in ("openai", "httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def main() -> None:
    _configure_replay_logging()
    parser = argparse.ArgumentParser(
        description="只读回放历史 Agent Run 的模型时间旁路结果"
    )
    parser.add_argument("--tenant-id", type=_positive_int, required=True)
    parser.add_argument("--since-hours", type=_positive_int, default=720)
    parser.add_argument("--run-limit", type=_positive_int, default=5000)
    parser.add_argument("--sample-size", type=_positive_int, default=20)
    parser.add_argument("--trial-count", type=_trial_count, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    created_after = datetime.now() - timedelta(hours=args.since_hours)
    with Session(engine) as session:
        load_result = load_temporal_replay_candidates(
            session,
            tenant_id=args.tenant_id,
            created_after=created_after,
            run_limit=args.run_limit,
            sample_size=args.sample_size,
        )
    service = TemporalInterpretationService(build_question_model_service())
    trial_result = replay_temporal_trials(
        load_result.candidates,
        service,
        trial_count=args.trial_count,
    )
    report = build_temporal_replay_report(
        tenant_id=args.tenant_id,
        created_after=created_after,
        load_result=load_result,
        trial_result=trial_result,
    )
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(content, end="")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
