"""阶段 7：硬切流门禁、质量门槛与运行门槛（doc38 §11.3.3–§11.3.5）。

三层判定全部基于持久化事实，不依赖模型输出：

- 硬门禁（§11.3.3）逐条 zero-or-100%，对每一次双跑记录单独评估；
  其中"未经 PROVEN 执行查询为 0"由执行服务端强制 + 失败观察扫描共同保证；
- 质量门槛（§11.3.4）是跨多次双跑的聚合指标，阈值从评测配置读取；
  阶段 0 基线数值未写入配置前处于未配置状态，直接阻断切流判定，
  绝不在没有数据时发明阈值；
- 运行门槛（§11.3.5）校验 p95 延迟、平均用量与超时率。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from apps.chatbi.services.research.comparison import (
    DualRunComparison,
    compare_dual_runs,
    normalize_agent_side,
)

GATE_SCOPE_VIOLATION = "scope_violation"
GATE_TARGET_METRIC_DRIFT = "target_metric_drift"
GATE_IMMUTABLE_FILTER_DRIFT = "immutable_filter_drift"
GATE_TIME_DRIFT = "time_drift"
GATE_CROSS_RUN_CITATION = "cross_run_citation"
GATE_UNSOURCED_NUMBER = "unsourced_number"
GATE_SILENT_FALLBACK = "silent_fallback"
GATE_UNPROVEN_QUERY = "unproven_query"
GATE_REPORT_CITATION_RATE = "report_citation_pass_rate"

# 静默回退与计划证明失败在观察错误码中的特征。
_FALLBACK_CODE_MARKERS = ("FALLBACK",)
_PROOF_FAILED_CODE = "PLAN_PROOF_FAILED"

DECISION_PROMOTE = "promote"
DECISION_BLOCKED = "blocked"


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    observed: str
    threshold: str
    value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "observed": self.observed,
            "threshold": self.threshold,
            "value": self.value,
        }


@dataclass
class HardGateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(item.passed for item in self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [item.as_dict() for item in self.results],
        }


# --------------------------------------------------------------------- #
# 硬门禁（§11.3.3）
# --------------------------------------------------------------------- #


def evaluate_hard_gates(
    legacy_derived: dict[str, Any],
    agent_derived: dict[str, Any],
    *,
    comparison: DualRunComparison | None = None,
) -> HardGateReport:
    """对一次双跑记录执行九条硬门禁。"""

    resolved = comparison or compare_dual_runs(legacy_derived, agent_derived)
    agent = normalize_agent_side(agent_derived)
    results = [
        _gate_scope(legacy_derived, agent_derived),
        _gate_dimension(
            GATE_TARGET_METRIC_DRIFT,
            _dimension_verdict(resolved, "input", "target_metrics"),
        ),
        _gate_dimension(
            GATE_IMMUTABLE_FILTER_DRIFT,
            _dimension_verdict(resolved, "input", "immutable_filters"),
        ),
        _gate_dimension(
            GATE_TIME_DRIFT,
            _dimension_verdict(resolved, "input", "time_roles"),
        ),
        _gate_cross_run(agent_derived),
        _gate_unsourced(agent_derived),
        _gate_code_scan(GATE_SILENT_FALLBACK, _FALLBACK_CODE_MARKERS, agent),
        _gate_code_scan(GATE_UNPROVEN_QUERY, (_PROOF_FAILED_CODE,), agent),
        _gate_report_citations(agent_derived),
    ]
    return HardGateReport(results=results)


def _gate_scope(
    legacy_derived: dict[str, Any],
    agent_derived: dict[str, Any],
) -> GateResult:
    violations = {
        "legacy": _scope_violations(legacy_derived, legacy=True),
        "agent": _scope_violations(agent_derived, legacy=False),
    }
    total = sum(len(items) for items in violations.values())
    detail = "; ".join(
        f"{side}:{items}" for side, items in violations.items() if items
    )
    return GateResult(
        GATE_SCOPE_VIOLATION,
        total == 0,
        detail or f"两侧证据引用均在 Scope 内（共检查 {total} 处越界=0）",
        "0",
        float(total),
    )


def _scope_violations(derived_state: dict[str, Any], *, legacy: bool) -> list[str]:
    """返回越界引用列表；空列表表示全部受控。"""

    if legacy:
        requirement = _requirement_payload(derived_state, marker_key=False)
        evidences = [
            item
            for item in (derived_state.get("research_evidence") or [])
            if isinstance(item, dict)
        ]
    else:
        requirement = _requirement_payload(derived_state, marker_key=True)
        snapshot = dict(derived_state.get("research_run_snapshot") or {})
        evidences = [
            item
            for item in (snapshot.get("evidences") or [])
            if isinstance(item, dict)
        ]
    if not requirement:
        # 冻结输入整体缺失时无法核对任何包含关系：直接记为越界，
        # 绝不因没有可检查的证据引用而空转通过。
        return ["FROZEN_INPUT_MISSING"]
    scope = requirement.get("scope") or {}
    allowed = set()
    for key in (
        "target_metric_refs",
        "dimension_refs",
        "driver_metric_refs",
        "contribution_metric_refs",
        "contribution_dimension_refs",
    ):
        for ref in scope.get(key) or ():
            allowed.add(str(ref))
    if not allowed:
        # Scope 内没有任何可用资产时，所有证据引用都不可证明。
        return [str(ref) for item in evidences for ref in _evidence_refs(item)]
    violations: set[str] = set()
    for item in evidences:
        for ref in _evidence_refs(item):
            if ref not in allowed:
                violations.add(ref)
    return sorted(violations)


def _requirement_payload(
    derived_state: dict[str, Any],
    *,
    marker_key: bool,
) -> dict[str, Any]:
    if marker_key:
        from apps.chatbi.services.research.shadow import SHADOW_MARKER_KEY

        marker = derived_state.get(SHADOW_MARKER_KEY)
        requirement_payload = marker.get("requirement") if isinstance(marker, dict) else None
        if isinstance(requirement_payload, dict):
            resolved_requirement: dict[str, Any] = requirement_payload
            return resolved_requirement
        # 评测双跑的 agent 主路径行没有 shadow 标记；冻结 Requirement 锚在
        # 新形状 research_state 的 requirement 字段上。回退读取与比较器
        # normalize_agent_side 同口径；两处都没有时按缺失记越界。
        embedded = (derived_state.get("research_state") or {}).get("requirement")
        if isinstance(embedded, dict):
            return embedded
        return {}
    execution = derived_state.get("execution_requirement")
    if isinstance(execution, dict):
        requirement = execution.get("research_requirement")
        if isinstance(requirement, dict):
            return requirement
    return {}


def _evidence_refs(evidence: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("metric_refs", "dimension_refs"):
        for ref in evidence.get(key) or ():
            refs.append(str(ref))
    return refs


def _gate_dimension(gate: str, verdict: str | None) -> GateResult:
    drift = 0 if verdict == "equal" else 1
    return GateResult(
        gate,
        verdict == "equal",
        f"维度判定={verdict}",
        "0",
        float(drift),
    )


def _dimension_verdict(
    comparison: DualRunComparison,
    group: str,
    dimension: str,
) -> str | None:
    for item in comparison.verdicts(group):
        if item.dimension == dimension:
            return item.verdict
    return None


def _gate_cross_run(agent_derived: dict[str, Any]) -> GateResult:
    """快照内所有证据及其依赖必须属于同一 run id。"""

    snapshot = dict(agent_derived.get("research_run_snapshot") or {})
    run_id = snapshot.get("run_id")
    evidences = [item for item in (snapshot.get("evidences") or []) if isinstance(item, dict)]
    cross: list[str] = [
        str(item.get("evidence_id"))
        for item in evidences
        if item.get("run_id") != run_id
    ]
    by_id = {item.get("evidence_id"): item for item in evidences}
    for item in evidences:
        for dependency in item.get("dependencies") or ():
            if not isinstance(dependency, dict):
                continue
            source = by_id.get(dependency.get("evidence_id"))
            if source is None or dependency.get("run_id") != run_id:
                cross.append(str(item.get("evidence_id")))
    cross = sorted(set(cross))
    return GateResult(
        GATE_CROSS_RUN_CITATION,
        not cross,
        f"跨 Run 引用={cross}" if cross else "证据台账无跨 Run 引用",
        "0",
        float(len(cross)),
    )


def _gate_unsourced(agent_derived: dict[str, Any]) -> GateResult:
    unsourced = _report_unsourced_citations(agent_derived)
    return GateResult(
        GATE_UNSOURCED_NUMBER,
        not unsourced,
        f"无来源引用={unsourced}" if unsourced else "报告引用均可溯源到本 Run 证据",
        "0",
        float(len(unsourced)),
    )


def _gate_code_scan(
    gate: str,
    markers: tuple[str, ...],
    agent: Any,
) -> GateResult:
    hits = [
        code
        for code in agent.error_codes
        if any(marker in str(code) for marker in markers)
    ]
    return GateResult(
        gate,
        not hits,
        f"命中错误码={hits}" if hits else "失败观察中无该类错误码",
        "0",
        float(len(hits)),
    )


def _gate_report_citations(agent_derived: dict[str, Any]) -> GateResult:
    unsourced = _report_unsourced_citations(agent_derived)
    checked, bad = _count_report_citations(agent_derived)
    rate = 100.0 if checked == 0 else round((checked - len(unsourced)) * 100.0 / checked, 2)
    passed = math.isclose(rate, 100.0) and not bad
    return GateResult(
        GATE_REPORT_CITATION_RATE,
        passed,
        f"引用 {checked} 条，通过率 {rate}%",
        "100%",
        rate,
    )


def _snapshot_evidence_ids(agent_derived: dict[str, Any]) -> set[str]:
    snapshot = dict(agent_derived.get("research_run_snapshot") or {})
    return {
        str(item.get("evidence_id"))
        for item in (snapshot.get("evidences") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    }


def _iter_report_findings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for payload_key in ("final_report", "report_draft"):
        payload_raw = snapshot.get(payload_key)
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except ValueError:
                continue
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            continue
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(finding)
    return findings


def _report_unsourced_citations(agent_derived: dict[str, Any]) -> list[str]:
    known = _snapshot_evidence_ids(agent_derived)
    unsourced: set[str] = set()
    for finding in _iter_report_findings(dict(agent_derived.get("research_run_snapshot") or {})):
        for citation in _finding_citations(finding):
            if citation not in known:
                unsourced.add(citation)
    return sorted(unsourced)


def _finding_citations(finding: dict[str, Any]) -> list[str]:
    citations: list[str] = []
    for key in ("citations", "evidence_ids"):
        for citation in finding.get(key) or ():
            if isinstance(citation, dict):
                citation = citation.get("evidence_id")
            if citation:
                citations.append(str(citation))
    return citations


def _count_report_citations(agent_derived: dict[str, Any]) -> tuple[int, int]:
    snapshot = dict(agent_derived.get("research_run_snapshot") or {})
    known = _snapshot_evidence_ids(agent_derived)
    total = 0
    bad = 0
    for finding in _iter_report_findings(snapshot):
        for citation in _finding_citations(finding):
            total += 1
            if citation not in known:
                bad += 1
    return total, bad


# --------------------------------------------------------------------- #
# 质量门槛（§11.3.4）
# --------------------------------------------------------------------- #

QUALITY_METRIC_KEYS = (
    "key_evidence_hit_rate_min",
    "silent_error_rate_max",
    "correct_reject_rate_min",
    "conclusion_support_rate_min",
    "duplicate_invalid_query_rate_max",
)


@dataclass(frozen=True)
class QualityThresholdSet:
    values: dict[str, float]
    configured_keys: tuple[str, ...]

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> QualityThresholdSet:
        values = {
            key: float(config[key])
            for key in QUALITY_METRIC_KEYS
            if config is not None and config.get(key) is not None
        }
        return cls(values=values, configured_keys=tuple(sorted(values)))

    @property
    def fully_configured(self) -> bool:
        return set(self.configured_keys) == set(QUALITY_METRIC_KEYS)

    def evaluate(self, metrics: dict[str, float]) -> list[GateResult]:
        """按"不低于旧路径 + 绝对最低要求"的组合语义评估聚合指标。

        ``metrics`` 的键约定：五个阈值键去掉方向后缀（_min/_max）。
        未配置的阈值产生未通过判定并阻断切流，绝不默认放行。
        """

        results: list[GateResult] = []
        for key in QUALITY_METRIC_KEYS:
            metric = key.rsplit("_", 1)[0]
            direction = key.rsplit("_", 1)[1]
            observed = metrics.get(metric)
            threshold = self.values.get(key)
            if threshold is None:
                results.append(
                    GateResult(
                        f"quality:{metric}",
                        False,
                        "评测配置缺少阶段 0 基线阈值，未配置不放行",
                        "not_configured",
                        None,
                    )
                )
                continue
            if observed is None:
                results.append(
                    GateResult(
                        f"quality:{metric}",
                        False,
                        "样本不足，无法计算该指标",
                        str(threshold),
                        None,
                    )
                )
                continue
            if direction == "min":
                passed = observed >= threshold
                relation = ">="
            else:
                passed = observed <= threshold
                relation = "<="
            results.append(
                GateResult(
                    f"quality:{metric}",
                    passed,
                    f"{observed:.4f} {relation} {threshold}",
                    str(threshold),
                    observed,
                )
            )
        return results


# --------------------------------------------------------------------- #
# 运行门槛（§11.3.5）
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuntimeLimits:
    p95_latency_ms_max: int
    avg_queries_max: float
    avg_model_calls_max: float


@dataclass
class RuntimeSample:
    latency_ms: int | None = None
    queries_used: int | None = None
    model_calls_used: int | None = None
    timed_out: bool = False


def evaluate_runtime_gates(
    samples: list[RuntimeSample],
    legacy_samples: list[RuntimeSample],
    limits: RuntimeLimits,
) -> list[GateResult]:
    results: list[GateResult] = []

    def _p95(values: list[int]) -> float:
        ordered = sorted(values)
        index = max(math.ceil(0.95 * len(ordered)) - 1, 0)
        return float(ordered[index])

    latencies = [item.latency_ms for item in samples if item.latency_ms is not None]
    if latencies and len(latencies) >= 20:
        p95 = _p95(latencies)
        results.append(
            GateResult(
                "runtime:p95_latency",
                p95 <= limits.p95_latency_ms_max,
                f"p95={p95:.0f}ms",
                f"<={limits.p95_latency_ms_max}ms",
                p95,
            )
        )

    def _avg(getter: Any) -> float | None:
        values = [getter(item) for item in samples]
        values = [float(value) for value in values if value is not None]
        return sum(values) / len(values) if values else None

    avg_queries = _avg(lambda item: item.queries_used)
    if avg_queries is not None:
        results.append(
            GateResult(
                "runtime:avg_queries",
                avg_queries <= limits.avg_queries_max,
                f"avg={avg_queries:.2f}",
                f"<={limits.avg_queries_max}",
                avg_queries,
            )
        )
    avg_model_calls = _avg(lambda item: item.model_calls_used)
    if avg_model_calls is not None:
        results.append(
            GateResult(
                "runtime:avg_model_calls",
                avg_model_calls <= limits.avg_model_calls_max,
                f"avg={avg_model_calls:.2f}",
                f"<={limits.avg_model_calls_max}",
                avg_model_calls,
            )
        )

    def _timeout_rate(rows: list[RuntimeSample]) -> float | None:
        if not rows:
            return None
        return sum(1 for item in rows if item.timed_out) / len(rows)

    new_rate = _timeout_rate(samples)
    legacy_rate = _timeout_rate(legacy_samples)
    if new_rate is not None and legacy_rate is not None:
        results.append(
            GateResult(
                "runtime:timeout_rate",
                new_rate <= legacy_rate,
                f"new={new_rate:.4f} legacy={legacy_rate:.4f}",
                "<=legacy",
                new_rate,
            )
        )
    return results


# --------------------------------------------------------------------- #
# 切流就绪汇总
# --------------------------------------------------------------------- #


@dataclass
class RolloutReadinessReport:
    hard_gate_reports: list[HardGateReport] = field(default_factory=list)
    quality_results: list[GateResult] = field(default_factory=list)
    runtime_results: list[GateResult] = field(default_factory=list)

    @property
    def hard_passed(self) -> bool:
        return bool(self.hard_gate_reports) and all(
            item.passed for item in self.hard_gate_reports
        )

    @property
    def quality_passed(self) -> bool:
        return bool(self.quality_results) and all(
            item.passed for item in self.quality_results
        )

    @property
    def runtime_passed(self) -> bool:
        return bool(self.runtime_results) and all(
            item.passed for item in self.runtime_results
        )

    @property
    def decision(self) -> str:
        return DECISION_PROMOTE if (
            self.hard_passed and self.quality_passed and self.runtime_passed
        ) else DECISION_BLOCKED

    @property
    def blockers(self) -> list[str]:
        blocked: list[str] = []
        if not self.hard_passed:
            blocked.extend(
                result.gate
                for report in self.hard_gate_reports
                for result in report.results
                if not result.passed
            )
        blocked.extend(
            result.gate for result in self.quality_results if not result.passed
        )
        blocked.extend(
            result.gate for result in self.runtime_results if not result.passed
        )
        return blocked

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "blockers": self.blockers,
            "hard_gates": [report.as_dict() for report in self.hard_gate_reports],
            "quality": [item.as_dict() for item in self.quality_results],
            "runtime": [item.as_dict() for item in self.runtime_results],
        }


__all__ = [
    "DECISION_BLOCKED",
    "DECISION_PROMOTE",
    "GATE_REPORT_CITATION_RATE",
    "QUALITY_METRIC_KEYS",
    "QualityThresholdSet",
    "RolloutReadinessReport",
    "RuntimeLimits",
    "RuntimeSample",
    "evaluate_hard_gates",
    "evaluate_runtime_gates",
]
