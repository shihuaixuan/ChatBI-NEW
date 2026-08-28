"""阶段 7 切流就绪判定集成（doc38 §11.3.3–§11.3.5）。

:mod:`gates` 定义了三层判定语义，本模块把"评测配置加载 → 双跑样本 →
聚合计值 → 就绪结论"接到持久化事实上，补齐生产侧缺口：

- :func:`load_quality_thresholds` 读 ``CHATBI_RESEARCH_EVAL_CONFIG`` 指向的
  JSON（形状：``{"quality": {五个阈值键}, "runtime": {运行上限}}``）。
  路径为空按"未配置"处理——质量门槛全部 ``not_configured`` 阻断，绝不在
  没有基线数据时发明阈值；文件缺失或 JSON 非法则显式报错：判定是离线
  动作，fail loud，绝不静默降级成未配置。
- :class:`DualPair` 描述一对双跑事实，由调用方从 DB（shadow 标记行）或
  评测结果文件组装；本模块保持纯函数，不触库、不调模型。
- :func:`compute_quality_metrics` 按 §11.3.4 五个聚合指标计值。口径全部
  锚定持久化字段并写明在 docstring；同一口径既用于基线提案也用于正式
  判定，保证阈值与观测可比。
- :func:`build_readiness_report` 组装逐 pair 硬门禁 + 质量聚合 + 运行
  门槛为 :class:`RolloutReadinessReport`，附诊断信息返回。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.chatbi.services.research.comparison import (
    RunFacts,
    normalize_agent_side,
    normalize_legacy_side,
)
from apps.chatbi.services.research.gates import (
    DECISION_PROMOTE,
    GATE_REPORT_CITATION_RATE,
    QUALITY_METRIC_KEYS,
    HardGateReport,
    QualityThresholdSet,
    RolloutReadinessReport,
    RuntimeLimits,
    RuntimeSample,
    evaluate_hard_gates,
    evaluate_runtime_gates,
)

# 新路径"声称成功"的状态集合：这些状态下证据为零即视为静默错误。
_AGENT_SUCCESS_STATUSES = frozenset({"succeeded", "partial"})
# 计入"无效查询率"的查询型工具；finish/compute 等非查询失败不计入。
_QUERY_TOOL_NAMES = frozenset({"query_semantic_data", "compute_evidence"})
_TIMEOUT_MARKER = "TIMEOUT"


@dataclass(frozen=True)
class DualPair:
    """一次新旧双跑的两侧派生状态。

    ``source`` 区分样本来源：``shadow``（生产流量后台双跑）与 ``eval``
    （评测脚本对同题双跑）。判定逻辑对来源不敏感——比较器只看持久化事实。
    """

    legacy_derived: dict[str, Any]
    agent_derived: dict[str, Any]
    parent_run_id: int | None = None
    agent_run_id: int | None = None
    source: str = "shadow"


def load_quality_thresholds(config_path: str | None) -> QualityThresholdSet:
    """从配置文件加载质量阈值；未配置返回空集（全量阻断）。

    文件顶层可以是 ``{"quality": {...}, "runtime": {...}}``，也可以直接是
    阈值键平铺对象；两种形状都归一到 ``QualityThresholdSet.from_config``。
    """

    if not config_path:
        return QualityThresholdSet.from_config(None)
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"RESEARCH_EVAL_CONFIG_MISSING:{config_path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"RESEARCH_EVAL_CONFIG_INVALID_JSON:{config_path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"RESEARCH_EVAL_CONFIG_INVALID_SHAPE:{config_path}")
    section = payload.get("quality")
    if section is None:
        section = {
            key: payload[key] for key in QUALITY_METRIC_KEYS if payload.get(key) is not None
        }
    if not isinstance(section, dict):
        raise ValueError(f"RESEARCH_EVAL_CONFIG_QUALITY_SECTION_INVALID:{config_path}")
    return QualityThresholdSet.from_config(section)


def load_runtime_limits(config_path: str | None) -> RuntimeLimits | None:
    """从同一配置文件的 ``runtime`` 段加载运行上限；缺段返回 None。"""

    if not config_path:
        return None
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"RESEARCH_EVAL_CONFIG_MISSING:{config_path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    section = (payload or {}).get("runtime") if isinstance(payload, dict) else None
    if not isinstance(section, dict) or not section:
        return None
    return RuntimeLimits(
        p95_latency_ms_max=int(section["p95_latency_ms_max"]),
        avg_queries_max=float(section["avg_queries_max"]),
        avg_model_calls_max=float(section["avg_model_calls_max"]),
    )


# --------------------------------------------------------------------- #
# 质量指标计值（§11.3.4）
# --------------------------------------------------------------------- #


def _pair_facts(pairs: list[DualPair]) -> list[tuple[RunFacts, RunFacts, DualPair]]:
    return [
        (normalize_legacy_side(pair.legacy_derived), normalize_agent_side(pair.agent_derived), pair)
        for pair in pairs
    ]


def _agent_rejected(facts: RunFacts) -> bool:
    """agent 侧显式拒绝：没有成功态也没有任何证据落地。"""

    return facts.evidence_count == 0 and facts.status not in _AGENT_SUCCESS_STATUSES


def _has_research_facts(legacy_facts: RunFacts, agent_facts: RunFacts) -> bool:
    """任一侧有研究循环事实即视为可判定 pair。

    两侧都没有（如路由期拒绝的评测对照）说明研究循环根本没启动，
    不进质量分母、也不做硬门禁——那不是双跑差异，是引擎无关行为。
    一侧有一侧没有反而是最严重的回归信号，必须参与硬门禁。
    """

    return bool(
        legacy_facts.status
        or legacy_facts.evidence_count
        or agent_facts.status
        or agent_facts.evidence_count
    )


def compute_quality_metrics(
    pairs: list[DualPair],
    hard_reports: list[HardGateReport] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """按 §11.3.4 计算五个聚合指标，附诊断信息。

    口径（全部锚定持久化事实，两侧一致适用）：

    - ``key_evidence_hit_rate``（min）：冻结目标指标被 agent 侧证据覆盖的
      pair 占比。未声明目标指标的 pair 计为未命中——没有可验证的关键
      证据要求本身就是要暴露的缺陷；
    - ``silent_error_rate``（max）：agent 侧声称 succeeded/partial 但零证据
      的 pair 占比；
    - ``correct_reject_rate``（min）：legacy 显式拒绝（failed 且零证据）的
      pair 中，agent 同样拒绝的占比；分母为零时该指标缺席（样本不足）；
    - ``conclusion_support_rate``（min）：agent 侧有报告的 pair 中，报告
      引用全部落在自身证据内的占比（复用硬门禁
      ``report_citation_pass_rate`` 的逐 pair 结论）；
    - ``duplicate_invalid_query_rate``（max）：全局合并的无效查询观察数 /
      （指纹请求数 + 无效观察数）。真实重复请求由服务端指纹与重复熔断在
      执行前拦截，不会落库，因此该指标以"无效查询占比"为持久化代理。

    两侧都无研究事实的 pair（如路由期拒绝的评测对照）不进质量分母，
    数量记录在诊断 ``excluded_engine_agnostic`` 中，不做静默截断。
    """

    facts = _pair_facts(pairs)
    reports = (
        hard_reports
        if hard_reports is not None
        else [evaluate_hard_gates(pair.legacy_derived, pair.agent_derived) for _, _, pair in facts]
    )
    excluded = [
        pair.parent_run_id or pair.agent_run_id
        for legacy_facts, agent_facts, pair in facts
        if not _has_research_facts(legacy_facts, agent_facts)
    ]
    evaluated = [
        (legacy_facts, agent_facts, report)
        for (legacy_facts, agent_facts, _pair), report in zip(
            facts, reports, strict=True
        )
        if _has_research_facts(legacy_facts, agent_facts)
    ]
    metrics: dict[str, float] = {}
    diagnostics: dict[str, Any] = {
        "total_pairs": len(pairs),
        "evaluated_pairs": len(evaluated),
        "excluded_engine_agnostic": excluded,
    }
    if not evaluated:
        return metrics, diagnostics

    denominator = len(evaluated)
    metrics["key_evidence_hit_rate"] = sum(
        1
        for _, agent_facts, _ in evaluated
        if set(agent_facts.target_metric_refs)
        and set(agent_facts.target_metric_refs) <= set(agent_facts.evidence_metric_refs)
    ) / denominator
    metrics["silent_error_rate"] = sum(
        1
        for _, agent_facts, _ in evaluated
        if agent_facts.status in _AGENT_SUCCESS_STATUSES and agent_facts.evidence_count == 0
    ) / denominator

    rejectable = [
        (legacy_facts, agent_facts)
        for legacy_facts, agent_facts, _ in evaluated
        if legacy_facts.status == "failed" and legacy_facts.evidence_count == 0
    ]
    if rejectable:
        metrics["correct_reject_rate"] = sum(
            1 for _, agent_facts in rejectable if _agent_rejected(agent_facts)
        ) / len(rejectable)

    reported = [
        (agent_facts, report)
        for _, agent_facts, report in evaluated
        if agent_facts.report_present
    ]
    if reported:
        metrics["conclusion_support_rate"] = sum(
            1
            for _, report in reported
            if any(
                item.gate == GATE_REPORT_CITATION_RATE and item.passed
                for item in report.results
            )
        ) / len(reported)

    invalid_queries = 0
    fingerprint_requests = 0
    for _legacy_facts, _agent_facts, pair in facts:
        state = pair.agent_derived.get("research_state") or {}
        fingerprints = state.get("request_fingerprints")
        if isinstance(fingerprints, dict):
            fingerprint_requests += len(fingerprints)
        state = pair.agent_derived.get("research_state") or {}
        for attempt in state.get("attempted_actions") or []:
            if (
                isinstance(attempt, dict)
                and attempt.get("status") in {"failed", "rejected"}
                and attempt.get("action_type") in _QUERY_TOOL_NAMES
            ):
                invalid_queries += 1
    metrics["duplicate_invalid_query_rate"] = invalid_queries / max(
        fingerprint_requests + invalid_queries, 1
    )

    return metrics, diagnostics


def build_runtime_samples(
    pairs: list[DualPair],
    *,
    latency_lookup: Callable[[int], int | None] | None = None,
    timeout_lookup: Callable[[int], bool] | None = None,
) -> tuple[list[RuntimeSample], list[RuntimeSample]]:
    """从双跑事实构建运行门槛样本（新路径在前）。

    延迟与超时不在派生状态内：调用方可注入按 run id 查询的回调；缺省时
    对应维度留空，运行门槛自动跳过无数据项（gates 内建行为），不做猜测。
    """

    def _sample(facts: RunFacts, run_id: int | None) -> RuntimeSample:
        timed_out = bool(timeout_lookup and run_id and timeout_lookup(run_id))
        latency = latency_lookup(run_id) if latency_lookup and run_id else None
        return RuntimeSample(
            latency_ms=int(latency) if latency is not None else None,
            queries_used=facts.queries_used,
            model_calls_used=facts.model_calls_used,
            timed_out=timed_out,
        )

    new_samples: list[RuntimeSample] = []
    legacy_samples: list[RuntimeSample] = []
    for pair in pairs:
        legacy_facts = normalize_legacy_side(pair.legacy_derived)
        agent_facts = normalize_agent_side(pair.agent_derived)
        legacy_samples.append(_sample(legacy_facts, pair.parent_run_id))
        new_samples.append(_sample(agent_facts, pair.agent_run_id))
    return new_samples, legacy_samples


def _split_evaluable(pairs: list[DualPair]) -> tuple[list[DualPair], list[Any]]:
    """把 pair 分成"研究循环真实运行过"与"引擎无关对照"两组。"""

    evaluated: list[DualPair] = []
    excluded: list[Any] = []
    for pair in pairs:
        legacy_facts = normalize_legacy_side(pair.legacy_derived)
        agent_facts = normalize_agent_side(pair.agent_derived)
        if _has_research_facts(legacy_facts, agent_facts):
            evaluated.append(pair)
        else:
            excluded.append(pair.parent_run_id or pair.agent_run_id)
    return evaluated, excluded


def build_readiness_report(
    pairs: list[DualPair],
    *,
    thresholds: QualityThresholdSet,
    runtime_limits: RuntimeLimits | None = None,
    latency_lookup: Callable[[int], int | None] | None = None,
    timeout_lookup: Callable[[int], bool] | None = None,
) -> tuple[RolloutReadinessReport, dict[str, Any]]:
    """三层判定的总装入口：硬门禁逐 pair、质量聚合、运行门槛。

    返回 ``(report, diagnostics)``；``diagnostics`` 含指标值、样本量与排除
    明细，供报告消费方核对而不是只看 promote/blocked 结论。
    """

    hard_reports: list[HardGateReport] = []
    evaluated_pairs, excluded_ids = _split_evaluable(pairs)
    for pair in evaluated_pairs:
        hard_reports.append(evaluate_hard_gates(pair.legacy_derived, pair.agent_derived))
    metrics, diagnostics = compute_quality_metrics(evaluated_pairs, hard_reports=hard_reports)
    diagnostics["total_pairs"] = len(pairs)
    diagnostics["excluded_engine_agnostic"] = excluded_ids
    quality_results = thresholds.evaluate(metrics)
    runtime_results: list[Any] = []
    if runtime_limits is not None and evaluated_pairs:
        new_samples, legacy_samples = build_runtime_samples(
            evaluated_pairs,
            latency_lookup=latency_lookup,
            timeout_lookup=timeout_lookup,
        )
        runtime_results = evaluate_runtime_gates(new_samples, legacy_samples, runtime_limits)
    report = RolloutReadinessReport(
        hard_gate_reports=hard_reports,
        quality_results=quality_results,
        runtime_results=runtime_results,
    )
    diagnostics["quality_metrics"] = metrics
    diagnostics["thresholds_configured"] = sorted(thresholds.values)
    diagnostics["decision"] = report.decision
    diagnostics["promotable"] = report.decision == DECISION_PROMOTE
    return report, diagnostics


__all__ = [
    "DualPair",
    "build_readiness_report",
    "build_runtime_samples",
    "compute_quality_metrics",
    "load_quality_thresholds",
    "load_runtime_limits",
]
