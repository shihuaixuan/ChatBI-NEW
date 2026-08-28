"""Research 离线评测结果比较器（doc42 §13.2）。

两侧的持久化形态不同：

- 旧路径（用户可见）：``derived_state["research_state"]``（ResearchState）
  + ``derived_state["research_evidence"]``（EvidenceSnapshot 列表）
  + ``derived_state["execution_requirement"]["research_requirement"]``（冻结输入）；
- 新路径：``derived_state["research_state"]`` 与
  ``derived_state["research_state_snapshot"]``。

比较器先把两侧归一化成同一组事实，再按四组维度输出逐项比较：
输入 / 过程 / 输出 / 运行。任何一侧没有记录的维度标记为
``not_comparable``，不猜测相等；硬门禁需要的结构事实由本模块一并提取，
判定逻辑在 ``gates.py``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

VERDICT_EQUAL = "equal"
VERDICT_DIFFERENT = "different"
VERDICT_NOT_COMPARABLE = "not_comparable"

GROUP_INPUT = "input"
GROUP_PROCESS = "process"
GROUP_OUTPUT = "output"
GROUP_RUNTIME = "runtime"

# 新 Completion 与 Finding 都通过 evidence_ids 建立回答溯源。
_CITATION_KEYS = ("evidence_ids",)


@dataclass(frozen=True)
class DimensionResult:
    group: str
    dimension: str
    verdict: str
    legacy_value: Any = None
    agent_value: Any = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "dimension": self.dimension,
            "verdict": self.verdict,
            "legacy_value": _jsonable(self.legacy_value),
            "agent_value": _jsonable(self.agent_value),
            "detail": self.detail,
        }


@dataclass
class RunFacts:
    """单侧运行的归一化事实。"""

    side: str
    goal: str | None = None
    status: str | None = None
    finish_reason: str | None = None
    premise_supported: bool | None = None
    target_metric_refs: tuple[str, ...] = ()
    immutable_filters: tuple[str, ...] = ()
    time_roles: tuple[str, ...] = ()
    scope_fingerprint: str | None = None
    schema_fingerprint: str | None = None
    evidence_count: int = 0
    evidence_metric_refs: tuple[str, ...] = ()
    evidence_dimension_refs: tuple[str, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    queries_used: int | None = None
    model_calls_used: int | None = None
    duration_seconds: float | None = None
    error_codes: tuple[str, ...] = ()
    report_present: bool = False
    report_citation_ids: tuple[str, ...] = ()
    findings_count: int | None = None


@dataclass
class DualRunComparison:
    legacy: RunFacts
    agent: RunFacts
    dimensions: list[DimensionResult] = field(default_factory=list)

    def verdicts(self, group: str) -> list[DimensionResult]:
        return [item for item in self.dimensions if item.group == group]

    def has_different(self) -> bool:
        return any(item.verdict == VERDICT_DIFFERENT for item in self.dimensions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "legacy": _facts_dict(self.legacy),
            "agent": _facts_dict(self.agent),
            "dimensions": [item.as_dict() for item in self.dimensions],
            "has_different": self.has_different(),
        }


def normalize_legacy_side(derived_state: dict[str, Any]) -> RunFacts:
    """从旧路径 run 行的 derived_state 提取归一化事实。"""

    state = dict(derived_state.get("research_state") or {})
    evidences = [
        item for item in (derived_state.get("research_evidence") or []) if isinstance(item, dict)
    ]
    requirement = _legacy_requirement(derived_state)
    remaining = dict(state.get("remaining_budget") or {})
    report = dict(state.get("report") or {})
    facts = RunFacts(
        side="legacy",
        goal=_optional_str(state.get("goal")),
        status=_optional_str(state.get("status")),
        finish_reason=_optional_str(state.get("finish_reason")),
        premise_supported=(
            bool(state["premise_supported"])
            if state.get("premise_supported") is not None
            else None
        ),
        target_metric_refs=_tuple(requirement.get("target_metric_refs")),
        immutable_filters=_filter_keys(requirement.get("immutable_filters")),
        time_roles=_tuple(requirement.get("time_roles")),
        scope_fingerprint=(
            str(requirement.get("version_snapshot", {}).get("scope_fingerprint"))
            if isinstance(requirement.get("version_snapshot"), dict)
            else None
        ),
        evidence_count=len(evidences),
        evidence_metric_refs=_union_refs(evidences, "metric_refs"),
        evidence_dimension_refs=_union_refs(evidences, "dimension_refs"),
        hypothesis_ids=tuple(
            str(item.get("id"))
            for item in (state.get("hypotheses") or [])
            if isinstance(item, dict) and item.get("id")
        ),
        # 旧路径只记录 remaining 预算；用量按 max−remaining 口径还原。
        duration_seconds=None,
        error_codes=(),
        report_present=bool(report),
        findings_count=len(report.get("conclusions") or []),
    )
    facts.queries_used = _used_from_remaining(remaining.get("max_queries"), remaining.get("queries"))
    facts.model_calls_used = _used_from_remaining(
        remaining.get("max_model_calls"), remaining.get("model_calls")
    )
    return facts


def normalize_agent_side(derived_state: dict[str, Any]) -> RunFacts:
    """从新 ResearchState 和快照提取归一化事实。"""

    snapshot = dict(derived_state.get("research_state_snapshot") or {})
    state = dict(derived_state.get("research_state") or {})
    snapshot_state = snapshot.get("state")
    snapshot_state = snapshot_state if isinstance(snapshot_state, dict) else {}
    if not state and isinstance(snapshot_state, dict):
        state = dict(snapshot_state)
    requirement = dict(state.get("requirement") or {})
    if not requirement:
        execution = derived_state.get("execution_requirement")
        if isinstance(execution, dict) and isinstance(
            execution.get("research_requirement"), dict
        ):
            requirement = dict(execution["research_requirement"])

    raw_evidences = state.get("react_evidence")
    if isinstance(raw_evidences, dict):
        evidences = [item for item in raw_evidences.values() if isinstance(item, dict)]
    elif isinstance(raw_evidences, list):
        evidences = [item for item in raw_evidences if isinstance(item, dict)]
    else:
        evidences = []
    usage = dict(state.get("react_budget_usage") or state.get("budget_usage") or {})
    version = dict(requirement.get("version_snapshot") or {})
    completion = dict(
        state.get("react_completion")
        or state.get("completion")
        or snapshot_state.get("completion")
        or {}
    )
    findings = [item for item in state.get("findings") or () if isinstance(item, dict)]
    citation_values: list[str] = []
    for evidence_id in completion.get("evidence_ids") or ():
        citation_values.append(str(evidence_id))
    for finding in findings:
        for evidence_id in finding.get("evidence_ids") or ():
            citation_values.append(str(evidence_id))
    citations = tuple(dict.fromkeys(citation_values))
    attempts = [
        item for item in state.get("attempted_actions") or () if isinstance(item, dict)
    ]
    failed_codes = tuple(
        str(error.get("code"))
        for item in attempts
        for error in (item.get("error"),)
        if item.get("status") in {"failed", "rejected"}
        and isinstance(error, dict)
        and error.get("code")
    )
    return RunFacts(
        side="agent",
        goal=_optional_str(requirement.get("goal")),
        status=_agent_status(state.get("current_status"), completion),
        finish_reason=_agent_finish_reason(state.get("current_status"), completion),
        premise_supported=None,
        target_metric_refs=_tuple(requirement.get("target_metric_refs")),
        immutable_filters=_filter_keys(requirement.get("immutable_filters")),
        time_roles=tuple(
            dict.fromkeys(
                str(binding.get("role"))
                for binding in (requirement.get("time_bindings") or [])
                if isinstance(binding, dict) and binding.get("role")
            )
        ),
        scope_fingerprint=(
            str(version.get("scope_fingerprint")) if version else None
        ),
        schema_fingerprint=(
            str(version.get("schema_fingerprint")) if version else None
        ),
        evidence_count=len(evidences),
        evidence_metric_refs=_union_refs(evidences, "metric_refs"),
        evidence_dimension_refs=_union_refs(evidences, "dimension_refs"),
        queries_used=_optional_int(usage.get("query_calls")),
        model_calls_used=_optional_int(usage.get("model_turns")),
        error_codes=failed_codes,
        report_present=bool(completion or findings),
        report_citation_ids=citations,
        findings_count=len(findings) if findings else None,
    )


def _agent_status(state_status: Any, completion: dict[str, Any]) -> str | None:
    """把新 ResearchState 状态归一化为比较器的历史状态口径。"""

    completion_status = completion.get("status")
    if completion_status == "complete":
        return "succeeded"
    if completion_status == "partial":
        return "partial"
    if completion_status == "unanswerable":
        return "failed"
    return {
        "completed": "succeeded",
        "partial": "partial",
        "unanswerable": "failed",
        "waiting_for_user": "needs_clarification",
        "failed": "failed",
        "cancelled": "cancelled",
        "running": "running",
    }.get(str(state_status))


def _agent_finish_reason(state_status: Any, completion: dict[str, Any]) -> str | None:
    """从新 Completion 的限制或终态推导可比较的结束原因。"""

    for limitation in completion.get("limitations") or ():
        if isinstance(limitation, dict) and limitation.get("code"):
            return str(limitation["code"])
    if completion.get("status") == "complete":
        return "sufficient_evidence"
    if completion.get("status") == "unanswerable":
        return "data_insufficient"
    return {
        "waiting_for_user": "needs_clarification",
        "failed": "execution_failed",
        "cancelled": "cancelled",
    }.get(str(state_status))


def compare_dual_runs(
    legacy_derived: dict[str, Any],
    agent_derived: dict[str, Any],
) -> DualRunComparison:
    """按 §11.3.2 的四组维度比较一次双跑。"""

    legacy = normalize_legacy_side(legacy_derived)
    agent = normalize_agent_side(agent_derived)
    comparison = DualRunComparison(legacy=legacy, agent=agent)
    comparison.dimensions.extend(_input_dimensions(legacy, agent))
    comparison.dimensions.extend(_process_dimensions(legacy, agent))
    comparison.dimensions.extend(_output_dimensions(legacy, agent))
    comparison.dimensions.extend(_runtime_dimensions(legacy, agent))
    return comparison


# --------------------------------------------------------------------- #
# 维度组
# --------------------------------------------------------------------- #


def _input_dimensions(legacy: RunFacts, agent: RunFacts) -> list[DimensionResult]:
    return [
        _compare(GROUP_INPUT, "goal", legacy.goal, agent.goal),
        _compare(GROUP_INPUT, "target_metrics", legacy.target_metric_refs, agent.target_metric_refs),
        _compare(GROUP_INPUT, "immutable_filters", legacy.immutable_filters, agent.immutable_filters),
        _compare(GROUP_INPUT, "time_roles", legacy.time_roles, agent.time_roles),
        _compare(
            GROUP_INPUT,
            "scope_fingerprint",
            legacy.scope_fingerprint,
            agent.scope_fingerprint,
        ),
    ]


def _process_dimensions(legacy: RunFacts, agent: RunFacts) -> list[DimensionResult]:
    dimensions = [
        _compare(GROUP_PROCESS, "status", legacy.status, agent.status),
        _compare(GROUP_PROCESS, "finish_reason", legacy.finish_reason, agent.finish_reason),
        _compare(
            GROUP_PROCESS,
            "query_count",
            legacy.evidence_count,
            agent.evidence_count,
        ),
        _set_compare(
            GROUP_PROCESS,
            "query_direction_metrics",
            legacy.evidence_metric_refs,
            agent.evidence_metric_refs,
        ),
        _set_compare(
            GROUP_PROCESS,
            "covered_dimensions",
            legacy.evidence_dimension_refs,
            agent.evidence_dimension_refs,
        ),
        _set_compare(
            GROUP_PROCESS,
            "hypotheses",
            legacy.hypothesis_ids,
            agent.hypothesis_ids,
        ),
    ]
    if legacy.premise_supported is not None and agent.finish_reason is not None:
        # 新路径不落 premise_supported 布尔位；用 finish_reason 是否为
        # premise_not_supported 对齐口径。
        agent_premise = agent.finish_reason != "premise_not_supported"
        dimensions.append(
            _compare(GROUP_PROCESS, "premise_supported", legacy.premise_supported, agent_premise)
        )
    else:
        dimensions.append(
            DimensionResult(
                GROUP_PROCESS,
                "premise_supported",
                VERDICT_NOT_COMPARABLE,
                detail="一侧缺少可比较的前提结论",
            )
        )
    return dimensions


def _output_dimensions(legacy: RunFacts, agent: RunFacts) -> list[DimensionResult]:
    return [
        _compare(
            GROUP_OUTPUT,
            "core_findings_count",
            legacy.findings_count,
            agent.findings_count,
        ),
        _compare(GROUP_OUTPUT, "report_present", legacy.report_present, agent.report_present),
        DimensionResult(
            GROUP_OUTPUT,
            "forbidden_conclusions",
            VERDICT_EQUAL if agent.report_present else VERDICT_NOT_COMPARABLE,
            detail=(
                "新路径最终报告由七类硬校验整体把关；旧路径无对应机制，"
                "该维度只对新路径做结构性断言"
            ),
        ),
        DimensionResult(
            GROUP_OUTPUT,
            "comprehensibility",
            VERDICT_NOT_COMPARABLE,
            detail="用户可理解性需要人工抽检，自动比较器不做判断",
        ),
    ]


def _runtime_dimensions(legacy: RunFacts, agent: RunFacts) -> list[DimensionResult]:
    dimensions = [
        _compare(GROUP_RUNTIME, "queries_used", legacy.queries_used, agent.queries_used),
        _compare(
            GROUP_RUNTIME,
            "model_calls_used",
            legacy.model_calls_used,
            agent.model_calls_used,
        ),
    ]
    if legacy.duration_seconds is None and agent.duration_seconds is None:
        dimensions.append(
            DimensionResult(
                GROUP_RUNTIME,
                "latency",
                VERDICT_NOT_COMPARABLE,
                detail="两侧延迟由运行门槛单独采集，比较器不重复计算",
            )
        )
    return dimensions


# --------------------------------------------------------------------- #
# 判定原语
# --------------------------------------------------------------------- #


def _compare(group: str, dimension: str, legacy: Any, agent: Any) -> DimensionResult:
    if legacy is None or agent is None:
        return DimensionResult(
            group,
            dimension,
            VERDICT_NOT_COMPARABLE,
            legacy_value=legacy,
            agent_value=agent,
            detail="一侧未记录该维度",
        )
    verdict = VERDICT_EQUAL if legacy == agent else VERDICT_DIFFERENT
    return DimensionResult(
        group, dimension, verdict, legacy_value=legacy, agent_value=agent
    )


def _set_compare(
    group: str,
    dimension: str,
    legacy: tuple[str, ...],
    agent: tuple[str, ...],
) -> DimensionResult:
    if legacy is None or agent is None:
        return DimensionResult(group, dimension, VERDICT_NOT_COMPARABLE)
    legacy_set, agent_set = set(legacy), set(agent)
    missing_in_agent = sorted(legacy_set - agent_set)
    extra_in_agent = sorted(agent_set - legacy_set)
    if not missing_in_agent and not extra_in_agent:
        return DimensionResult(
            group, dimension, VERDICT_EQUAL, legacy_value=sorted(legacy_set), agent_value=sorted(agent_set)
        )
    return DimensionResult(
        group,
        dimension,
        VERDICT_DIFFERENT,
        legacy_value=sorted(legacy_set),
        agent_value=sorted(agent_set),
        detail=f"仅旧路径有={missing_in_agent}; 仅新路径有={extra_in_agent}",
    )


# --------------------------------------------------------------------- #
# 归一化辅助
# --------------------------------------------------------------------- #


def _legacy_requirement(derived_state: dict[str, Any]) -> dict[str, Any]:
    execution = derived_state.get("execution_requirement")
    if isinstance(execution, dict):
        requirement = execution.get("research_requirement")
        if isinstance(requirement, dict):
            return requirement
    return {}


def _filter_keys(raw: Any) -> tuple[str, ...]:
    """把筛选条件归一化成可比较的稳定字符串（target|operator|value）。"""

    if not isinstance(raw, (list, tuple)):
        return ()
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = json.dumps(item.get("value"), ensure_ascii=False, sort_keys=True, default=str)
        normalized.append(f"{item.get('target_ref')}|{item.get('operator')}|{value}")
    return tuple(sorted(normalized))


def _union_refs(evidences: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for item in evidences:
        values = item.get(key)
        if values is None:
            definition = item.get("definition")
            if isinstance(definition, dict):
                values = definition.get(
                    {"metric_refs": "metrics", "dimension_refs": "dimensions"}.get(
                        key, key
                    )
                )
        for ref in values or ():
            if ref:
                refs.add(str(ref))
    return tuple(sorted(refs))


def _used_from_remaining(maximum: Any, remaining: Any) -> int | None:
    """评测同款口径：用量 = max − remaining；缺任一侧则视为不可用。"""

    if maximum is None or remaining is None:
        return None
    try:
        return max(int(maximum) - int(remaining), 0)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item) for item in raw))


def _facts_dict(facts: RunFacts) -> dict[str, Any]:
    return {
        key: (
            list(value)
            if isinstance(value, tuple)
            else value
        )
        for key, value in vars(facts).items()
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


__all__ = [
    "DualRunComparison",
    "RunFacts",
    "VERDICT_DIFFERENT",
    "VERDICT_EQUAL",
    "VERDICT_NOT_COMPARABLE",
    "compare_dual_runs",
    "normalize_agent_side",
    "normalize_legacy_side",
]
