"""Research 阶段 0 统一评测：旧路径基线 + 不变量自动判分（38 号文档 §4.3）。

用例来自 ``data/research_agent_eval_cases.json``（结构化 Gold），通过生产入口
``create_agent_start_events`` 运行旧 ResearchAction 路径，然后对每个 Run 输出统一
评测记录并自动判分：

1. 用例断言：目标指标、时间角色、必需/禁止证据模式、结束原因、Scope 越界；
2. 全局不变量（对应 §4.3.3 类别 13/15/18/19/20/23）：Evidence 所有权、无引用结论、
   重复动作执行、报告数字溯源、相关性写成因果、Evidence 依赖顺序；
3. 运行指标：迭代数、模型调用、查询数、耗时、错误码分布（§4.3.4 运行指标）。

结果分为五类，显式失败和静默错误被明确区分：
``pass`` / ``correct_reject`` / ``explicit_failure`` / ``silent_error`` / ``harness_error``。

用法：
    python scripts/run_research_agent_eval.py --list-cases
    python scripts/run_research_agent_eval.py --case research-cause-003
    python scripts/run_research_agent_eval.py --output data/research_agent_eval_baseline_20260822.json

环境前置与 run_research_stage3_real_questions.py 相同：真实数据库 + 真实模型，
``CHAT_AGENT_EXECUTION_MODES`` 需要包含 research（脚本已自动设置）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# 必须在导入任何应用模块前设置：Settings 在导入时实例化。
os.environ["CHAT_AGENT_ENABLED"] = "true"
os.environ["CHAT_AGENT_EXECUTION_MODES"] = "fast,plan,research"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlmodel import Session  # noqa: E402

from apps.chatbi.composition import (  # noqa: E402
    build_chat_application_service,
    configure_agent_cleanup,
)
from apps.chatbi.models.dto.agent import AgentStartStreamRequest  # noqa: E402
from apps.chatbi.orchestration.agent.service import (  # noqa: E402
    create_agent_start_events,
)
from apps.chatbi.repository.sqlmodel.agent_run_repository import (  # noqa: E402
    AgentExecutionDeletionService,
)
from apps.conversation import CreateChat  # noqa: E402
from common.core.db import engine  # noqa: E402

CASES_FILE = Path(__file__).parent / "data" / "research_agent_eval_cases.json"
SCRIPT_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_RESULTS_FILE = BACKEND_ROOT / "data" / "research_agent_eval_results.json"
OBSERVABILITY_CONTRACT_VERSION = "phase0-v2"

# claim_level 为 common_change/correlation_clue 的 finding 中禁止出现的因果断言词。
CAUSAL_PATTERN = re.compile(r"(导致|造成|因为|由于|归因|主要原因是|所致|驱动了)")

# 这些类别必须绑定到实际注册的判分器，不能只在用例上贴类别标签。
# 全局类别由 check_global_invariants 输出；用例类别由 check_case_assertions 或
# check_required_patterns 输出。新增类别时先补判分器，再把类别加入用例 Gold。
GLOBAL_CATEGORY_CHECKS = {
    13: (
        "invariant:no_cross_run_or_unknown_citations",
        "invariant:evidence_ownership_observed",
    ),
    15: "invariant:no_duplicate_action_execution",
    18: "invariant:findings_have_citations",
    19: "invariant:report_numbers_from_cited_evidence",
    20: "invariant:no_causal_claim_on_correlation",
    23: "invariant:evidence_dependency_ordering",
}
CASE_CATEGORY_REQUIREMENTS = {
    1: {"required_patterns": {"premise_confirmation"}},
    2: {"required_patterns": {"baseline_single_period"}},
    3: {"required_patterns": {"result_driven_filter"}},
    4: {"required_patterns": {"hierarchy_drilldown"}},
    6: {"required_patterns": {"contribution_reconciled"}},
    7: {"required_patterns": {"driver_validation"}},
    8: {"required_keys": {"forbid_driver_metric_refs"}},
    9: {"required_keys": {"require_data_insufficient_outcome"}},
    10: {"required_keys": {"require_data_insufficient_outcome"}},
    12: {
        "any_keys": {"forbid_dimension_refs", "forbid_driver_metric_refs"},
    },
    16: {"required_keys": {"require_budget_exhaustion"}},
    17: {"required_keys": {"require_partial_success_failure"}},
    22: {"required_patterns": {"parallel_directions"}},
}


# ---------------------------------------------------------------------------
# 用例加载


@dataclass(slots=True)
class EvalCase:
    """一个评测问题及其结构化 Gold。"""

    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def name(self) -> str:
        return str(self.raw["name"])

    @property
    def categories(self) -> list[int]:
        return list(self.raw.get("categories") or [])

    @property
    def question(self) -> str:
        return str(self.raw["question"])

    @property
    def expected(self) -> dict[str, Any]:
        return dict(self.raw.get("expected") or {})

    @property
    def budgets(self) -> dict[str, Any]:
        return dict(self.raw.get("budgets") or {})


def load_cases(path: Path) -> tuple[dict[str, Any], list[EvalCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [EvalCase(raw=item) for item in payload.get("cases") or []]
    if not cases:
        raise SystemExit(f"评测集为空: {path}")
    errors = validate_case_set(payload, cases)
    if errors:
        raise SystemExit("评测集契约无效:\n" + "\n".join(f"- {item}" for item in errors))
    return payload, cases


def validate_case_set(payload: dict[str, Any], cases: list[EvalCase]) -> list[str]:
    """校验用例唯一性、预算和类别覆盖声明。"""

    errors: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        errors.append(f"用例 ID 重复: {duplicates}")
    for case in cases:
        targets = case.expected.get("target_metric_refs") or []
        if not targets:
            errors.append(f"{case.id} 缺少 expected.target_metric_refs")
        for name in ("max_queries", "max_model_calls"):
            value = case.budgets.get(name)
            if not isinstance(value, int) or value <= 0:
                errors.append(f"{case.id}.budgets.{name} 必须为正整数")
        invalid_categories = [category for category in case.categories if category not in range(1, 25)]
        if invalid_categories:
            errors.append(f"{case.id} 存在非法类别: {invalid_categories}")
        if case.expected.get("allowed_error_codes") and not case.expected.get(
            "allowed_rejection_stages"
        ):
            errors.append(f"{case.id} 含 allowed_error_codes 时必须声明 allowed_rejection_stages")

    explicit = {category for case in cases for category in case.categories}
    actual: set[int] = set(GLOBAL_CATEGORY_CHECKS)
    for category in sorted(explicit):
        if category in GLOBAL_CATEGORY_CHECKS:
            continue
        requirement = CASE_CATEGORY_REQUIREMENTS.get(category)
        if requirement is None:
            errors.append(f"类别 {category} 没有注册判分器，不能通过类别标签声明覆盖")
            continue
        matching_cases = [case for case in cases if category in case.categories]
        configured = False
        for matching_case in matching_cases:
            expected = matching_case.expected
            required_patterns = requirement.get("required_patterns", set())
            required_keys = requirement.get("required_keys", set())
            any_keys = requirement.get("any_keys", set())
            has_patterns = required_patterns <= set(
                expected.get("required_evidence_patterns") or ()
            )
            has_keys = all(bool(expected.get(key)) for key in required_keys)
            has_any_key = not any_keys or any(bool(expected.get(key)) for key in any_keys)
            if has_patterns and has_keys and has_any_key:
                configured = True
                break
        if not configured:
            errors.append(
                f"类别 {category} 的用例没有满足已注册判分器所需的 Gold 字段，不能声明覆盖"
            )
        else:
            actual.add(category)
    declared = set(payload.get("covered_categories") or ())
    if declared != actual:
        errors.append(
            f"covered_categories 与实际注册判分器不一致: declared={sorted(declared)}, actual={sorted(actual)}"
        )
    uncovered = {
        int(str(category).split("_", 1)[0])
        for category in (payload.get("uncovered_categories") or {})
        if str(category).split("_", 1)[0].isdigit()
    }
    overlap = sorted(declared & uncovered)
    if overlap:
        errors.append(f"covered_categories 与 uncovered_categories 重叠: {overlap}")
    return errors


# ---------------------------------------------------------------------------
# 生产入口执行（与 run_research_stage3_real_questions.py 相同的调用方式）


def _latest_run(session: Session, chat_id: int) -> dict[str, Any]:
    row = session.exec(
        text(
            "SELECT r.id, r.record_id, r.status, r.execution_mode, "
            "r.derived_state, r.config, r.budget_snapshot, r.temporal_context, "
            "r.error_class, r.error, "
            "r.created_at, r.updated_at "
            "FROM chatbi_agent_run r WHERE r.chat_id = :chat_id "
            "ORDER BY r.id DESC LIMIT 1"
        ).bindparams(chat_id=chat_id)
    ).mappings().one()
    return dict(row)


def _step_usage(session: Session, run_id: int) -> dict[str, Any]:
    rows = session.exec(
        text(
            "SELECT latency_ms, token_usage FROM chatbi_agent_step "
            "WHERE run_id = :run_id ORDER BY step_index"
        ).bindparams(run_id=run_id)
    ).all()
    latency = sum(int(item[0]) for item in rows if item[0] is not None)
    latency_available = bool(rows) and all(item[0] is not None for item in rows)
    tokens: dict[str, int | None] = {"input": None, "output": None}
    steps_with_tokens = 0
    for _, usage in rows:
        if isinstance(usage, dict):
            step_has_tokens = False
            for key in ("input", "output"):
                value = usage.get(key) or usage.get(f"{key}_tokens")
                if isinstance(value, int) and value >= 0:
                    tokens[key] = (tokens[key] or 0) + value
                    step_has_tokens = True
            if step_has_tokens:
                steps_with_tokens += 1
    return {
        "step_count": len(rows),
        "steps_with_token_usage": steps_with_tokens,
        "total_latency_ms": latency if latency_available else None,
        "token_usage": tokens,
        "availability": {
            "steps": True,
            "step_latency": latency_available,
            "token_usage": steps_with_tokens > 0,
        },
    }


def _scalar_observation(session: Session, sql: str, *, run_id: int) -> tuple[int | None, str | None]:
    """读取单个运行指标；数据库没有该观测入口时明确返回不可用原因。"""

    try:
        value = session.exec(text(sql).bindparams(run_id=run_id)).scalar_one()
    except SQLAlchemyError as exc:
        return None, f"query_failed:{type(exc).__name__}"
    if value is None:
        return None, "value_missing"
    return int(value), None


def _trace_runtime_usage(session: Session, run_id: int) -> dict[str, Any]:
    """从现有 Step、Tool Call 和 Trace 事实读取可观测运行指标。"""

    tool_calls, tool_error = _scalar_observation(
        session,
        "SELECT COUNT(*) FROM chatbi_agent_tool_call WHERE run_id = :run_id",
        run_id=run_id,
    )
    trace_nodes, trace_error = _scalar_observation(
        session,
        "SELECT COUNT(*) FROM chatbi_agent_trace_node WHERE run_id = :run_id",
        run_id=run_id,
    )
    llm_calls, llm_error = _scalar_observation(
        session,
        """
        SELECT COUNT(*)
        FROM chatbi_agent_trace_node
        WHERE run_id = :run_id
          AND (node_type IN ('llm', 'model') OR name IN ('llm', 'model', 'reason'))
        """,
        run_id=run_id,
    )
    if llm_error is None and llm_calls == 0 and (trace_nodes or 0) > 0:
        # 旧 Research 的 Trace 只有 Agent invocation/阶段节点，没有逐次模型调用事实。
        # 这里不能把 0 当作真实模型调用数，改为明确不可观测。
        llm_calls = None
        llm_error = "trace_has_no_model_call_nodes"
    return {
        "tool_calls": {
            "value": tool_calls,
            "available": tool_error is None,
            "unavailable_reason": tool_error,
        },
        "trace_nodes": {
            "value": trace_nodes,
            "available": trace_error is None,
            "unavailable_reason": trace_error,
        },
        "model_calls": {
            "value": llm_calls,
            "available": llm_error is None,
            "unavailable_reason": llm_error,
        },
    }


def _artifact_size(value: Any) -> int | None:
    """从已有 Artifact 引用读取服务端报告的字节大小，不对缺失值估算。"""

    if isinstance(value, dict):
        for key in ("size_bytes", "byte_size", "bytes", "size"):
            candidate = value.get(key)
            if isinstance(candidate, int) and candidate >= 0:
                return candidate
        for child in value.values():
            size = _artifact_size(child)
            if size is not None:
                return size
    elif isinstance(value, list):
        for child in value:
            size = _artifact_size(child)
            if size is not None:
                return size
    return None


def _runtime_usage(
    session: Session,
    run: dict[str, Any],
    state: dict[str, Any],
    case: EvalCase,
) -> dict[str, Any]:
    """形成 legacy/shadow/agent 共用的运行指标结构。"""

    run_id = run.get("id")
    step_usage = _step_usage(session, int(run_id)) if run_id is not None else {
        "step_count": None,
        "steps_with_token_usage": 0,
        "total_latency_ms": None,
        "token_usage": {"input": None, "output": None},
        "availability": {"steps": False, "step_latency": False, "token_usage": False},
    }
    trace_usage = (
        _trace_runtime_usage(session, int(run_id))
        if run_id is not None
        else {
            "tool_calls": {"value": None, "available": False, "unavailable_reason": "run_id_missing"},
            "trace_nodes": {"value": None, "available": False, "unavailable_reason": "run_id_missing"},
            "model_calls": {"value": None, "available": False, "unavailable_reason": "run_id_missing"},
        }
    )
    iterations = state.get("iteration_records") or []
    result_ids = [
        result_id
        for record in iterations
        for result_id in (record.get("result_ids") or [])
    ]
    plan_ids = [
        plan_id
        for record in iterations
        for plan_id in (record.get("plan_ids") or [])
    ]
    remaining = state.get("remaining_budget") or {}
    configured_budget = case.budgets
    query_lower_bound = len(result_ids)
    model_lower_bound = len(iterations)
    budget_checks = {
        "max_queries": configured_budget.get("max_queries"),
        "max_model_calls": configured_budget.get("max_model_calls"),
        "query_lower_bound": query_lower_bound,
        "model_call_lower_bound": model_lower_bound,
        "lower_bound_within_limit": (
            query_lower_bound <= configured_budget["max_queries"]
            and model_lower_bound <= configured_budget["max_model_calls"]
            if "max_queries" in configured_budget and "max_model_calls" in configured_budget
            else False
        ),
        "actual_values_observed": (
            trace_usage["model_calls"]["available"]
            or trace_usage["tool_calls"]["available"]
        ),
    }
    return {
        **step_usage,
        "tool_calls": trace_usage["tool_calls"],
        "trace_nodes": trace_usage["trace_nodes"],
        "model_calls": trace_usage["model_calls"],
        "queries": {
            "value": None,
            "available": False,
            "lower_bound": query_lower_bound,
            "unavailable_reason": "legacy_research_query_fact_not_persisted",
        },
        "model_calls_lower_bound": model_lower_bound,
        "queries_lower_bound": query_lower_bound,
        "plan_count": len(plan_ids),
        "result_count": len(result_ids),
        "budget_check": budget_checks,
        "budget_remaining_snapshot": remaining,
        "artifact_size_bytes": _artifact_size(run.get("derived_state")),
        "artifact_size_available": _artifact_size(run.get("derived_state")) is not None,
        # 旧字段保留，但不再用 remaining budget 伪造实际消耗。
        "model_calls_used_estimated": None,
        "queries_used_estimated": None,
    }


def _check_case_budget(case: EvalCase, usage: dict[str, Any]) -> dict[str, Any]:
    """将用例预算作为判分上限；观测缺失时只使用可证明的下界。"""

    max_queries = case.budgets.get("max_queries")
    max_model_calls = case.budgets.get("max_model_calls")
    query_value = (usage.get("queries") or {}).get("value")
    model_value = (usage.get("model_calls") or {}).get("value")
    query_count = query_value if isinstance(query_value, int) else usage.get("queries_lower_bound")
    model_count = model_value if isinstance(model_value, int) else usage.get("model_calls_lower_bound")
    complete = isinstance(max_queries, int) and isinstance(max_model_calls, int)
    ok = (
        complete
        and isinstance(query_count, int)
        and isinstance(model_count, int)
        and query_count <= max_queries
        and model_count <= max_model_calls
    )
    return {
        "name": "budget:case_limits_respected",
        "ok": ok,
        "available": complete,
        "detail": {
            "max_queries": max_queries,
            "max_model_calls": max_model_calls,
            "queries": query_count,
            "model_calls": model_count,
            "query_count_kind": "observed" if isinstance(query_value, int) else "lower_bound",
            "model_call_count_kind": "observed" if isinstance(model_value, int) else "lower_bound",
        },
    }


def _create_case_chat(
    session: Session,
    case: EvalCase,
    args: argparse.Namespace,
    defaults: dict[str, Any],
) -> int:
    configure_agent_cleanup(AgentExecutionDeletionService)
    dataset_id = args.dataset_id or int(defaults["dataset_id"])
    datasource_id = args.datasource_id or int(defaults["datasource_id"])
    chat = build_chat_application_service(session).create(
        user_id=args.user_id or int(defaults["user_id"]),
        workspace_id=args.tenant_id or int(defaults["tenant_id"]),
        request=CreateChat(
            dataset_id=dataset_id,
            datasource=datasource_id,
            question=f"Research 评测：{case.name}",
        ),
    )
    if chat.id is None:
        raise RuntimeError(f"评测会话创建成功但未返回 ID: case={case.id}")
    return int(chat.id)


def _run_production_entry(case: EvalCase, args: argparse.Namespace, defaults: dict[str, Any]) -> None:
    user = SimpleNamespace(id=args.user_id or int(defaults["user_id"]), oid=args.tenant_id or int(defaults["tenant_id"]))
    request = AgentStartStreamRequest(
        chat_id=args._chat_id,  # type: ignore[attr-defined]
        question=case.question,
        datasource_id=args.datasource_id or int(defaults["datasource_id"]),
    )
    for _event in create_agent_start_events(user, request):
        del _event


# ---------------------------------------------------------------------------
# 判分器：纯函数，输入 derived_state 摘要与用例 Gold，输出检查明细


def _evidence_order(evidence: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, int]:
    """按 iteration_records 还原 Evidence 创建顺序；退化时按列表顺序。"""
    order: dict[str, int] = {}
    index = 0
    for evidence_id in state.get("evidence_ids") or ():
        if evidence_id not in order:
            order[evidence_id] = index
            index += 1
    for record in state.get("iteration_records") or []:
        for evidence_id in record.get("evidence_ids") or []:
            if evidence_id not in order:
                order[evidence_id] = index
                index += 1
    for item in evidence:
        evidence_id = item.get("evidence_id")
        if evidence_id not in order:
            order[evidence_id] = index
            index += 1
    return order


def _expected_target_metrics(case: EvalCase, asset_refs: dict[str, Any]) -> set[str]:
    """读取用例自己的目标指标；缺失时才使用数据集默认值。"""

    expected = {
        str(ref)
        for ref in case.expected.get("target_metric_refs") or ()
        if str(ref).strip()
    }
    if expected:
        return expected
    default_target = asset_refs.get("target_metric")
    return {str(default_target)} if default_target else set()


def _evidence_for_targets(
    evidence: list[dict[str, Any]],
    target_metrics: set[str],
) -> list[dict[str, Any]]:
    return [
        item
        for item in evidence
        if target_metrics & set(item.get("metric_refs") or ())
    ]


def _is_data_insufficient(state: dict[str, Any], expected: dict[str, Any]) -> bool:
    """数据不足时允许缺少证据，但必须有明确终止语义。"""

    finish_reason = state.get("finish_reason")
    status = state.get("status")
    allowed_reasons = {
        "data_insufficient",
        "premise_not_supported",
        "needs_clarification",
        "execution_failed",
        "partial_failure",
    }
    return (
        finish_reason in allowed_reasons
        and finish_reason in set(expected.get("allowed_finish_reasons") or ())
        and status in set(expected.get("allowed_research_statuses") or ())
    )


def _numeric_variants(value: Any) -> set[str]:
    """把 Evidence 中的数字规范化为可比较表示。"""

    if isinstance(value, bool) or value is None:
        return set()
    text_value = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", text_value):
        return set()
    variants = {text_value.rstrip("%")}
    try:
        number = float(text_value.rstrip("%"))
    except ValueError:
        return variants
    if number.is_integer():
        variants.add(str(int(number)))
    else:
        variants.add(format(number, ".12g"))
    return variants


def _report_numbers(statement: str) -> list[str]:
    """提取报告数字，并排除日期和受控资产/证据编号。"""

    # 日期、资产引用、Evidence/Result/Plan 编号不是结果数字，不能拿来判定虚构数字。
    cleaned = re.sub(
        r"(?:METRIC|DIMENSION|evidence|result|plan|task):[A-Za-z0-9:_-]+",
        " ",
        statement,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b20\d{2}(?:[-/]\d{1,2}){1,2}\b", " ", cleaned)
    cleaned = re.sub(r"20\d{2}年\d{1,2}月\d{1,2}日?", " ", cleaned)
    cleaned = re.sub(r"(?:沿|第|批次|轮次|假设\s*h|h)\s*\d+", " ", cleaned, flags=re.IGNORECASE)
    return re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?", cleaned)


def _evidence_numeric_values(item: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    statistics = item.get("statistics") or {}
    for value in statistics.values():
        values |= _numeric_variants(value)
    for rows_key in ("top_rows", "bottom_rows"):
        for row in item.get(rows_key) or ():
            for cell in row.get("values") or ():
                values |= _numeric_variants(cell.get("value"))
    return values


def _check_report_numbers(
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """逐条检查报告数字是否能在其引用 Evidence 中找到。"""

    report = state.get("report") or {}
    evidence_by_id = {item.get("evidence_id"): item for item in evidence}
    fabricated: list[dict[str, Any]] = []
    observed = False
    for finding in report.get("findings") or ():
        statement = str(finding.get("statement") or "")
        numbers = _report_numbers(statement)
        if not numbers:
            continue
        observed = True
        source_values: set[str] = set()
        for evidence_id in finding.get("evidence_ids") or ():
            source = evidence_by_id.get(evidence_id)
            if source is not None:
                source_values |= _evidence_numeric_values(source)
        missing = [
            number
            for number in numbers
            if not any(
                variant in source_values
                for variant in _numeric_variants(number)
            )
        ]
        if missing:
            fabricated.append(
                {
                    "statement": statement,
                    "evidence_ids": list(finding.get("evidence_ids") or ()),
                    "numbers": missing,
                }
            )
    return {
        "name": "invariant:report_numbers_from_cited_evidence",
        "ok": not fabricated,
        "available": observed,
        **({"detail": fabricated} if fabricated else {}),
    }


def _dependency_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """读取生产快照或测试夹具中的 Evidence 依赖事实。"""

    records: list[dict[str, Any]] = []
    for key in ("research_evidence_dependencies", "evidence_dependencies", "action_dependencies"):
        value = state.get(key)
        if isinstance(value, dict):
            records.extend(
                dict(item, evidence_id=evidence_id)
                for evidence_id, item in value.items()
                if isinstance(item, dict)
            )
        elif isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    for iteration in state.get("iteration_records") or ():
        iteration_number = iteration.get("iteration")
        for item in iteration.get("evidence_dependencies") or ():
            if isinstance(item, dict):
                records.append(dict(item, iteration=iteration_number))
        for item in iteration.get("action_dependencies") or ():
            if isinstance(item, dict):
                records.append(dict(item, iteration=iteration_number))
    return records


def _check_evidence_dependency_order(
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """引用型动作只能依赖更早轮次的 Evidence。"""

    records = _dependency_records(state)
    if not records:
        if not evidence and not (state.get("iteration_records") or ()):
            return {
                "name": "invariant:evidence_dependency_ordering",
                "ok": True,
                "available": True,
                "detail": "research did not start; dependency check not applicable",
            }
        metadata_present = any(
            key in state
            for key in (
                "research_evidence_dependencies",
                "evidence_dependencies",
                "action_dependencies",
            )
        )
        if metadata_present:
            return {
                "name": "invariant:evidence_dependency_ordering",
                "ok": True,
                "available": True,
                "detail": "no reference action observed",
            }
        return {
            "name": "invariant:evidence_dependency_ordering",
            "ok": False,
            "available": False,
            "detail": "dependency facts are not persisted",
        }
    evidence_ids = {item.get("evidence_id") for item in evidence}
    order = _evidence_order(evidence, state)
    iteration_by_evidence: dict[str, int] = {}
    for iteration in state.get("iteration_records") or ():
        for evidence_id in iteration.get("evidence_ids") or ():
            iteration_by_evidence[evidence_id] = int(iteration.get("iteration") or 0)
    violations: list[dict[str, Any]] = []
    for record in records:
        evidence_id = record.get("evidence_id")
        dependencies = (
            record.get("depends_on_evidence_ids")
            or record.get("source_evidence_ids")
            or record.get("evidence_ids")
            or []
        )
        if not dependencies and not record.get("requires_dependency"):
            continue
        consumer_iteration = record.get("iteration")
        if consumer_iteration is None:
            consumer_iteration = iteration_by_evidence.get(evidence_id)
        if record.get("requires_dependency") and not dependencies:
            violations.append(
                {
                    "evidence_id": evidence_id,
                    "dependency_id": None,
                    "reason": "dependency_missing",
                }
            )
        for dependency_id in dependencies:
            dependency_iteration = iteration_by_evidence.get(dependency_id)
            if dependency_id not in evidence_ids:
                violations.append(
                    {"evidence_id": evidence_id, "dependency_id": dependency_id, "reason": "unknown"}
                )
            elif consumer_iteration is not None and dependency_iteration is not None:
                if dependency_iteration >= consumer_iteration:
                    violations.append(
                        {
                            "evidence_id": evidence_id,
                            "dependency_id": dependency_id,
                            "reason": "same_or_later_iteration",
                        }
                    )
            elif order.get(dependency_id, -1) >= order.get(evidence_id, -1):
                violations.append(
                    {"evidence_id": evidence_id, "dependency_id": dependency_id, "reason": "same_or_later_order"}
                )
    return {
        "name": "invariant:evidence_dependency_ordering",
        "ok": not violations,
        "available": True,
        **({"detail": violations} if violations else {}),
    }


def _column_roles(evidence: dict[str, Any]) -> set[str]:
    return {
        str(column.get("value_role"))
        for column in evidence.get("logical_columns") or ()
        if column.get("value_role")
    }


def _has_inherited_hierarchy_filter(
    evidence: list[dict[str, Any]],
    asset_refs: dict[str, Any],
) -> bool:
    """下钻证据必须携带上级维度的 applied filter（相邻下钻的观测特征）。"""
    parent = asset_refs.get("hierarchy_parent_dimension")
    child = asset_refs.get("hierarchy_child_dimension")
    if not parent or not child:
        return bool(evidence)
    return any(
        child in (item.get("dimension_refs") or [])
        and any(
            applied.get("target_ref") == parent
            for applied in (item.get("applied_filters") or ())
        )
        for item in evidence
    )


def _same_batch_covers(evidence: list[dict[str, Any]], required_dims: set[str]) -> bool:
    """并行方向判定：存在同一 batch 的证据，其维度并集覆盖全部要求维度。"""

    if not required_dims:
        return True  # 未声明维度时无法表达该要求，视为满足
    batches: dict[str, set[str]] = {}
    for item in evidence:
        batch_id = item.get("batch_id")
        if not batch_id:
            continue
        batches.setdefault(str(batch_id), set()).update(item.get("dimension_refs") or ())
    return any(required_dims <= covered for covered in batches.values())


def _decimal_value(value: Any) -> Decimal | None:
    """把 Evidence 数值转换为 Decimal，避免用字符串或资产编号参与对账。"""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").rstrip("%"))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _unique_evidence_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    """合并 top/bottom 样本并去重，防止同一行被两次计入对账。"""

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("top_rows", "bottom_rows"):
        for row in item.get(key) or ():
            identity = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            if identity not in seen:
                seen.add(identity)
                rows.append(row)
    return rows


def _evidence_complete_for_reconciliation(item: dict[str, Any]) -> bool:
    """只有完整且未截断的 Evidence 才能参与总量对账。"""

    statistics = item.get("statistics") or {}
    row_count = item.get("row_count", statistics.get("row_count"))
    truncated = item.get("truncated", statistics.get("truncated"))
    rows = _unique_evidence_rows(item)
    return (
        isinstance(row_count, int)
        and row_count > 0
        and truncated is False
        and len(rows) >= row_count
    )


def _role_values(item: dict[str, Any], role: str) -> list[Decimal]:
    """读取指定逻辑列的完整数值。"""

    indexes = {
        index
        for index, column in enumerate(item.get("logical_columns") or ())
        if column.get("value_role") == role
    }
    values: list[Decimal] = []
    for row in _unique_evidence_rows(item):
        for cell in row.get("values") or ():
            if cell.get("logical_column_index") not in indexes:
                continue
            value = _decimal_value(cell.get("value"))
            if value is not None:
                values.append(value)
    return values


def _check_contribution_reconciliation(
    evidence: list[dict[str, Any]],
    targets: set[str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """核对分组差值、总差值和贡献比例；样本不完整时明确标记不可用。"""

    candidates = [
        item
        for item in evidence
        if targets & set(item.get("metric_refs") or ())
    ]
    contribution_candidates = [
        item
        for item in candidates
        if "contribution" in _column_roles(item)
        and "difference" in _column_roles(item)
        and item.get("dimension_refs")
    ]
    breakdown_candidates = [
        item
        for item in candidates
        if "difference" in _column_roles(item)
        and item.get("dimension_refs")
        and "group_key" in _column_roles(item)
        and "contribution" not in _column_roles(item)
    ]
    total_candidates = [
        item
        for item in candidates
        if "difference" in _column_roles(item)
        and not item.get("dimension_refs")
        and "group_key" not in _column_roles(item)
    ]
    if not contribution_candidates or not breakdown_candidates or not total_candidates:
        return {
            "ok": False,
            "available": False,
            "detail": "contribution/breakdown/total Evidence 不完整",
        }

    def row_count(item: dict[str, Any]) -> int:
        statistics = item.get("statistics") or {}
        value = item.get("row_count", statistics.get("row_count"))
        return value if isinstance(value, int) else -1

    contribution = max(contribution_candidates, key=row_count)
    breakdown = max(breakdown_candidates, key=row_count)
    total = max(total_candidates, key=row_count)
    incomplete = [
        item
        for item in (contribution, breakdown, total)
        if not _evidence_complete_for_reconciliation(item)
    ]
    if incomplete:
        return {
            "ok": False,
            "available": False,
            "detail": {
                "reason": "Evidence 被截断或缺少完整行数",
                "evidence_ids": [item.get("evidence_id") for item in incomplete],
            },
        }

    difference_values = _role_values(breakdown, "difference")
    total_values = _role_values(total, "difference")
    contribution_values = _role_values(contribution, "contribution")
    if not difference_values or len(total_values) != 1 or not contribution_values:
        return {
            "ok": False,
            "available": False,
            "detail": "对账所需逻辑列数值缺失或总差值不唯一",
        }

    tolerance = _decimal_value(expected.get("contribution_reconciliation_tolerance", "1e-6"))
    if tolerance is None or tolerance < 0:
        return {
            "ok": False,
            "available": False,
            "detail": "贡献度容差配置无效",
        }
    total_difference = total_values[0]
    difference_sum = sum(difference_values, Decimal(0))
    contribution_sum = sum(contribution_values, Decimal(0))
    contribution_target = Decimal(1) if abs(contribution_sum - 1) <= abs(contribution_sum - 100) else Decimal(100)
    difference_error = abs(difference_sum - total_difference)
    contribution_error = abs(contribution_sum - contribution_target)
    return {
        "ok": difference_error <= tolerance and contribution_error <= tolerance,
        "available": True,
        "detail": {
            "difference_sum": str(difference_sum),
            "total_difference": str(total_difference),
            "difference_error": str(difference_error),
            "contribution_sum": str(contribution_sum),
            "contribution_target": str(contribution_target),
            "contribution_error": str(contribution_error),
            "tolerance": str(tolerance),
        },
    }


def _check_partial_success_failure(
    evidence: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    """部分成功必须同时观察到成功 Evidence 和失败动作。"""

    failed_actions = [
        failure
        for record in state.get("iteration_records") or ()
        for failure in record.get("failed_actions") or ()
        if isinstance(failure, dict)
    ]
    return {
        "ok": bool(evidence) and bool(failed_actions),
        "available": True,
        "detail": {
            "successful_evidence_count": len(evidence),
            "failed_action_count": len(failed_actions),
        },
    }


def _check_budget_exhaustion(
    case: EvalCase,
    run: dict[str, Any],
    state: dict[str, Any],
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    """预算耗尽必须由运行终态或可核对的预算剩余边界证明。"""

    config = run.get("config") or {}
    max_queries = case.budgets.get("max_queries")
    max_model_calls = case.budgets.get("max_model_calls")
    config_matches = (
        config.get("research_max_queries") == max_queries
        and config.get("research_max_model_calls") == max_model_calls
    )
    remaining = state.get("remaining_budget") or {}
    terminal_claim = state.get("finish_reason") == "budget_exhausted" or state.get(
        "status"
    ) == "budget_exhausted"
    boundary_observed = any(
        isinstance(remaining.get(key), int) and remaining.get(key) == 0
        for key in ("queries", "model_calls")
    )
    lower_bound = usage or {}
    query_lower_bound = lower_bound.get("queries_lower_bound")
    model_lower_bound = lower_bound.get("model_calls_lower_bound")
    boundary_proven = (
        isinstance(query_lower_bound, int)
        and isinstance(max_queries, int)
        and query_lower_bound >= max_queries
    ) or (
        isinstance(model_lower_bound, int)
        and isinstance(max_model_calls, int)
        and model_lower_bound >= max_model_calls
    )
    ok = config_matches and (terminal_claim or boundary_observed or boundary_proven)
    return {
        "name": "budget:exhaustion_observed",
        "ok": ok,
        "available": config_matches,
        "detail": {
            "config_matches_case": config_matches,
            "terminal_claim": terminal_claim,
            "remaining_budget": remaining,
            "boundary_observed": boundary_observed,
            "boundary_proven_by_lower_bound": boundary_proven,
        },
    }


def _check_data_insufficient_outcome(
    case: EvalCase,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """空结果或数据不足必须有专门的终止原因，不能由预算耗尽替代。"""

    allowed_reasons = {
        "data_insufficient",
        "premise_not_supported",
        "needs_clarification",
    }
    status = state.get("status")
    finish_reason = state.get("finish_reason")
    ok = (
        not evidence
        and finish_reason in allowed_reasons
        and finish_reason in set(case.expected.get("allowed_finish_reasons") or ())
        and status in set(case.expected.get("allowed_research_statuses") or ())
    )
    return {
        "name": "data:data_insufficient_outcome",
        "ok": ok,
        "available": True,
        "detail": {
            "evidence_count": len(evidence),
            "status": status,
            "finish_reason": finish_reason,
        },
    }


def check_required_patterns(
    case: EvalCase,
    evidence: list[dict[str, Any]],
    state: dict[str, Any],
    asset_refs: dict[str, Any],
) -> list[dict[str, Any]]:
    expected = case.expected
    checks: list[dict[str, Any]] = []
    targets = _expected_target_metrics(case, asset_refs)
    order = _evidence_order(evidence, state)
    contribution_check = _check_contribution_reconciliation(evidence, targets, expected)

    def has_premise() -> bool:
        if state.get("premise_supported"):
            return True
        return any(
            {"current", "previous"} <= set(item.get("time_roles") or ())
            and targets & set(item.get("metric_refs") or ())
            for item in evidence
        )

    pattern_impl: dict[str, bool] = {
        "premise_confirmation": has_premise(),
        "baseline_single_period": any(
            "single" in (item.get("time_roles") or [])
            and targets & set(item.get("metric_refs") or ())
            for item in evidence
        ),
        "dimension_breakdown": any(
            item.get("dimension_refs")
            and _column_roles(item) & {"difference", "growth_rate"}
            for item in evidence
        ),
        "result_driven_filter": any(
            order.get(item.get("evidence_id"), 0) > 0 and item.get("applied_filters")
            for item in evidence
        ),
        "hierarchy_drilldown": (
            bool(state.get("covered_hierarchy_ids"))
            and _has_inherited_hierarchy_filter(evidence, asset_refs)
        ),
        "contribution_reconciled": contribution_check["ok"],
        "driver_validation": False,
        "parallel_directions": _same_batch_covers(
            evidence, set(expected.get("require_dimension_refs") or ())
        ),
    }
    required_drivers = set(expected.get("require_driver_metric_refs") or ())
    if not required_drivers:
        pattern_impl["driver_validation"] = True
    else:
        covered = set(state.get("covered_driver_metric_refs") or ())
        linked = {
            driver
            for driver in required_drivers
            if any(
                driver in (item.get("metric_refs") or []) and item.get("hypothesis_ids")
                for item in evidence
            )
        }
        pattern_impl["driver_validation"] = required_drivers <= covered | linked

    for name in expected.get("required_evidence_patterns") or []:
        ok = pattern_impl.get(name, False)
        check = {"name": f"required_pattern:{name}", "ok": ok}
        if name == "contribution_reconciled":
            check["available"] = contribution_check.get("available", False)
            check["detail"] = contribution_check.get("detail")
        checks.append(check)
    for name in expected.get("forbidden_evidence_patterns") or []:
        ok = not pattern_impl.get(name, False)
        checks.append({"name": f"forbidden_pattern:{name}", "ok": ok})
    return checks


def _collect_error_codes(run: dict[str, Any], state: dict[str, Any]) -> set[str]:
    """从结构化运行字段收集错误码，避免用自然语言子串误判。"""

    values: list[Any] = [run.get("error_class"), run.get("error")]
    for record in state.get("iteration_records") or ():
        values.extend(
            failure.get("error_code")
            for failure in record.get("failed_actions") or ()
            if isinstance(failure, dict)
        )
    codes: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        codes.update(re.findall(r"[A-Z][A-Z0-9_]{2,}", value))
    return codes


def _rejection_stage(run: dict[str, Any], state: dict[str, Any]) -> str:
    """依据运行模式和结构化错误区分路由、准备和执行阶段。"""

    if run.get("execution_mode") != "research":
        error_codes = _collect_error_codes(run, state)
        # 资产未覆盖是在已选模式上构造执行需求时发现的，不是“选错模式”。
        if "SEMANTIC_EXECUTION_ASSET_NOT_COVERED" in error_codes:
            return "execution"
        routing_prefixes = (
            "EXECUTION_MODE_",
            "SEMANTIC_PARSE_",
            "SEMANTIC_CANDIDATE_",
            "SEMANTIC_ASSET_",
            "SEMANTIC_EXECUTABLE_MODEL_",
            "SEMANTIC_QUERY_REQUIREMENT_",
            "SEMANTIC_MULTI_STEP_",
            "LIMITED_MULTISTEP_",
        )
        if any(code.startswith(routing_prefixes) for code in error_codes):
            return "routing"
        if run.get("status") in {"failed", "cancelled"}:
            return "execution"
        return "routing"
    if not state:
        return "preparation"
    if state.get("status") in {"failed", "cancelled"}:
        return "execution"
    return "unknown"


def _is_explicit_rejection(
    run: dict[str, Any],
    state: dict[str, Any],
    expected: dict[str, Any],
    hit_error_code: str | None,
) -> bool:
    """允许错误码只有在实际失败/拒绝时才具有 correct_reject 语义。"""

    if hit_error_code is None:
        return False
    if run.get("status") not in {"failed", "cancelled"}:
        return False
    allowed_stages = set(expected.get("allowed_rejection_stages") or ())
    return bool(allowed_stages) and _rejection_stage(run, state) in allowed_stages


def check_case_assertions(
    case: EvalCase,
    run: dict[str, Any],
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
    asset_refs: dict[str, Any],
    hit_error_code: str | None = None,
    explicit_rejection: bool = False,
    usage: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    expected = case.expected
    checks: list[dict[str, Any]] = []
    status = state.get("status")
    finish_reason = state.get("finish_reason")
    targets = _expected_target_metrics(case, asset_refs)
    target_evidence = _evidence_for_targets(evidence, targets)

    def add(name: str, ok: bool, detail: str = "") -> None:
        entry: dict[str, Any] = {"name": name, "ok": ok}
        if detail:
            entry["detail"] = detail
        checks.append(entry)

    if expected.get("require_data_insufficient_outcome"):
        checks.append(_check_data_insufficient_outcome(case, state, evidence))
    if expected.get("require_partial_success_failure"):
        checks.append(_check_partial_success_failure(evidence, state))
    if expected.get("require_budget_exhaustion"):
        checks.append(_check_budget_exhaustion(case, run, state, usage))

    # 只有运行确实失败/拒绝且错误码精确匹配时，才跳过研究成功态断言。
    if not explicit_rejection:
        add(
            "execution_mode_is_research",
            run.get("execution_mode") == "research",
            f"got {run.get('execution_mode')}",
        )
        allowed_statuses = expected.get("allowed_research_statuses") or []
        if allowed_statuses:
            add(
                "research_status_allowed",
                status in set(allowed_statuses),
                f"got {status}, allowed={allowed_statuses}",
            )
        allowed_reasons = expected.get("allowed_finish_reasons") or []
        if allowed_reasons:
            add(
                "finish_reason_allowed",
                finish_reason in set(allowed_reasons),
                f"got {finish_reason}, allowed={allowed_reasons}",
            )
    else:
        add(
            "explicit_rejection_status",
            run.get("status") in {"failed", "cancelled"},
            f"got {run.get('status')}",
        )
        add(
            "explicit_rejection_stage",
            _rejection_stage(run, state) in set(expected.get("allowed_rejection_stages") or ()),
            f"stage={_rejection_stage(run, state)}, error_code={hit_error_code}",
        )

    # 时间保持：所有证据的时间角色不得超出用例声明范围。
    time_expected = set(expected.get("time_roles_expected") or ())
    if time_expected:
        overflow = {
            role
            for item in evidence
            for role in (item.get("time_roles") or [])
            if role not in time_expected
        }
        add(
            "time_roles_retained",
            not overflow,
            f"overflow={sorted(overflow)}" if overflow else "",
        )

        observed_roles = {
            role
            for item in target_evidence
            for role in item.get("time_roles") or ()
        }
        roles_missing = time_expected - observed_roles
        missing_ok = explicit_rejection or (
            not target_evidence and _is_data_insufficient(state, expected)
        )
        add(
            "time_roles_required_observed",
            not roles_missing or missing_ok,
            (
                f"missing={sorted(roles_missing)}"
                if roles_missing and not missing_ok
                else "not_observable_due_to_explicit_rejection_or_data_insufficient"
                if roles_missing
                else ""
            ),
        )

    # 目标指标保持：证据引用的指标必须在本用例目标 ∪ 已治理驱动指标范围内。
    allowed_metrics = targets | set(
        asset_refs.get("governed_driver_metrics") or []
    )
    stray = {
        ref
        for item in evidence
        for ref in (item.get("metric_refs") or [])
        if ref not in allowed_metrics
    }
    add("target_metric_scope_kept", not stray, f"stray={sorted(stray)}" if stray else "")

    observed_targets = {
        ref
        for item in target_evidence
        for ref in (item.get("metric_refs") or ())
    }
    missing_targets = targets - observed_targets
    target_missing_ok = explicit_rejection or (
        not target_evidence and _is_data_insufficient(state, expected)
    )
    add(
        "target_metric_required_observed",
        not missing_targets or target_missing_ok,
        (
            f"missing={sorted(missing_targets)}"
            if missing_targets and not target_missing_ok
            else "not_observable_due_to_explicit_rejection_or_data_insufficient"
            if missing_targets
            else ""
        ),
    )

    require_dims = set(expected.get("require_dimension_refs") or [])
    if require_dims and not explicit_rejection:
        hit = {
            ref
            for item in evidence
            for ref in (item.get("dimension_refs") or [])
            if ref in require_dims
        }
        add(
            "required_dimension_refs_covered",
            require_dims <= hit,
            f"missing={sorted(require_dims - hit)}",
        )

    forbid_dims = set(expected.get("forbid_dimension_refs") or [])
    if forbid_dims:
        leaked = {
            ref
            for item in evidence
            for ref in (item.get("dimension_refs") or [])
            if ref in forbid_dims
        }
        add("scope_violation_dimensions", not leaked, f"leaked={sorted(leaked)}" if leaked else "")

    forbid_drivers = set(expected.get("forbid_driver_metric_refs") or [])
    if forbid_drivers:
        leaked_drivers = forbid_drivers & set(state.get("covered_driver_metric_refs") or [])
        add(
            "forbidden_driver_not_covered",
            not leaked_drivers,
            f"leaked={sorted(leaked_drivers)}" if leaked_drivers else "",
        )

    if expected.get("require_hypotheses_with_evidence") and not explicit_rejection:
        hypotheses = state.get("hypotheses") or []
        terminal = [
            item
            for item in hypotheses
            if item.get("status") in {"supported", "weakened", "inconclusive"}
            and item.get("evidence_ids")
        ]
        add(
            "hypothesis_with_evidence_present",
            bool(terminal),
            f"hypotheses={len(hypotheses)}",
        )

    # 不可变筛选保持：声明了筛选值时，触及该维度的证据必须携带该筛选。
    immutable_filters = expected.get("immutable_filters") or []
    for binding in immutable_filters:
        target_ref = str(binding.get("target_ref"))
        value = str(binding.get("value"))

        def carries(
            item: dict[str, Any],
            _target_ref: str = target_ref,
            _value: str = value,
        ) -> bool:
            refs = set(item.get("dimension_refs") or [])
            if _target_ref not in refs:
                return True  # 未触及该维度则不适用
            for applied in item.get("applied_filters") or ():
                if applied.get("target_ref") == _target_ref and str(
                    applied.get("value")
                ) == str(_value):
                    return True
            return False

        violating = [item for item in evidence if not carries(item)]
        add(
            f"immutable_filter_retained:{target_ref}",
            not violating,
            f"{len(violating)} evidence touch dimension without filter" if violating else "",
        )

    return checks


def check_global_invariants(
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
    run_id: int | None = None,
) -> list[dict[str, Any]]:
    """跨用例统一判分的业务不变量（§4.3.3 类别 13/15/18/19/20/23）。"""
    checks: list[dict[str, Any]] = []
    report = state.get("report") or {}
    findings = report.get("findings") or []
    citations = report.get("citations") or []
    evidence_ids = {item.get("evidence_id") for item in evidence}

    dangling = sorted(
        {
            evidence_id
            for finding in findings
            for evidence_id in (finding.get("evidence_ids") or [])
            if evidence_id not in evidence_ids
        }
        | {
            citation.get("evidence_id")
            for citation in citations
            if citation.get("evidence_id") not in evidence_ids
        }
    )
    checks.append(
        {
            "name": "invariant:no_cross_run_or_unknown_citations",
            "ok": not dangling,
            **({"detail": f"dangling={dangling}"} if dangling else {}),
        }
    )

    ownership = (
        state.get("research_evidence_ownership")
        or state.get("evidence_ownership")
        or {}
    )
    ownership_violations = []
    ownership_unobserved = []
    referenced_ids = {
        evidence_id
        for finding in findings
        for evidence_id in (finding.get("evidence_ids") or ())
    } | {
        citation.get("evidence_id")
        for citation in citations
        if citation.get("evidence_id")
    }
    for evidence_id in referenced_ids:
        owner = ownership.get(evidence_id) if isinstance(ownership, dict) else None
        if owner is None:
            owner = next(
                (
                    item.get("run_id")
                    or (item.get("lineage") or {}).get("run_id")
                    for item in evidence
                    if item.get("evidence_id") == evidence_id
                ),
                None,
            )
        if owner is None or run_id is None:
            ownership_unobserved.append(evidence_id)
        elif run_id is not None and str(owner) != str(run_id):
            ownership_violations.append({"evidence_id": evidence_id, "owner": owner})
    checks.append(
        {
            "name": "invariant:evidence_ownership_observed",
            "ok": not ownership_unobserved and not ownership_violations,
            "available": not ownership_unobserved,
            **(
                {"detail": {"unobserved": ownership_unobserved, "wrong_owner": ownership_violations}}
                if ownership_unobserved or ownership_violations
                else {}
            ),
        }
    )

    uncited = [finding for finding in findings if not finding.get("evidence_ids")]
    checks.append(
        {
            "name": "invariant:findings_have_citations",
            "ok": not uncited,
            **({"detail": f"{len(uncited)} findings without citation"} if uncited else {}),
        }
    )

    fingerprints = list(state.get("executed_action_fingerprints") or ())
    duplicates = sorted({fp for fp in fingerprints if fingerprints.count(fp) > 1})
    checks.append(
        {
            "name": "invariant:no_duplicate_action_execution",
            "ok": not duplicates,
            **({"detail": f"duplicates={duplicates}"} if duplicates else {}),
        }
    )

    causal_findings = [
        finding
        for finding in findings
        if finding.get("claim_level") in {"common_change", "correlation_clue"}
        and CAUSAL_PATTERN.search(str(finding.get("statement") or ""))
    ]
    checks.append(
        {
            "name": "invariant:no_causal_claim_on_correlation",
            "ok": not causal_findings,
            **(
                {"detail": [item.get("statement") for item in causal_findings]}
                if causal_findings
                else {}
            ),
        }
    )

    checks.append(_check_report_numbers(state, evidence))
    checks.append(_check_evidence_dependency_order(state, evidence))
    return checks


# ---------------------------------------------------------------------------
# 统一评测记录与结果分类


@dataclass(slots=True)
class CaseRecord:
    case_id: str
    name: str
    outcome: str
    violations: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)


def classify_outcome(
    state: dict[str, Any],
    checks: list[dict[str, Any]],
    harness_error: str | None,
    explicit_rejection: bool = False,
) -> str:
    if harness_error:
        return "harness_error"
    failed_names = [item["name"] for item in checks if not item.get("ok")]
    status = state.get("status")
    claimed_success = status in {"succeeded", "partial", "budget_exhausted"}
    if explicit_rejection:
        # 只有错误码、失败状态和阶段均符合用例声明时，才分类为正确拒绝。
        return "correct_reject" if not failed_names else "silent_error"
    if failed_names:
        return "silent_error" if claimed_success else "explicit_failure"
    return "pass"


def evaluate_case(
    case: EvalCase,
    args: argparse.Namespace,
    defaults: dict[str, Any],
    asset_refs: dict[str, Any],
) -> CaseRecord:
    print(f"\n{'=' * 88}\n[case:{case.id}] {case.question}\n{'=' * 88}")
    harness_error: str | None = None
    run: dict[str, Any] = {}
    usage: dict[str, Any] = {
        "step_count": None,
        "steps_with_token_usage": 0,
        "total_latency_ms": None,
        "token_usage": {"input": None, "output": None},
        "availability": {
            "steps": False,
            "step_latency": False,
            "token_usage": False,
        },
        "tool_calls": {"value": None, "available": False, "unavailable_reason": "harness_error"},
        "trace_nodes": {"value": None, "available": False, "unavailable_reason": "harness_error"},
        "model_calls": {"value": None, "available": False, "unavailable_reason": "harness_error"},
        "queries": {
            "value": None,
            "available": False,
            "lower_bound": 0,
            "unavailable_reason": "harness_error",
        },
        "model_calls_lower_bound": 0,
        "queries_lower_bound": 0,
        "plan_count": 0,
        "result_count": 0,
        "budget_check": {
            "max_queries": case.budgets.get("max_queries"),
            "max_model_calls": case.budgets.get("max_model_calls"),
            "query_lower_bound": 0,
            "model_call_lower_bound": 0,
            "lower_bound_within_limit": False,
            "actual_values_observed": False,
        },
        "budget_remaining_snapshot": {},
        "artifact_size_bytes": None,
        "artifact_size_available": False,
        "model_calls_used_estimated": None,
        "queries_used_estimated": None,
    }
    try:
        with Session(engine) as session:
            chat_id = _create_case_chat(session, case, args, defaults)
        args._chat_id = chat_id  # type: ignore[attr-defined]
        _run_production_entry(case, args, defaults)
        with Session(engine) as session:
            run = _latest_run(session, chat_id)
            derived = run.get("derived_state") or {}
            usage = _runtime_usage(
                session,
                run,
                derived.get("research_state") or {},
                case,
            )
    except Exception as exc:  # 单个用例异常不阻断其余用例
        # 这是评测边界的显式隔离：记录 harness_error，继续执行后续用例，不能吞掉错误。
        harness_error = repr(exc)
        print(f"[case:{case.id}] HARNESS EXCEPTION: {exc!r}")

    derived = run.get("derived_state") or {}
    state = dict(derived.get("research_state") or {})
    version_snapshot = (
        (derived.get("execution_requirement") or {}).get("version_snapshot")
        or (
            (derived.get("execution_requirement") or {}).get("research_requirement")
            or {}
        ).get("version_snapshot")
        or (derived.get("research_requirement") or {}).get("version_snapshot")
        or {}
    )
    # 评测元数据与 ResearchState 分开保存，判分时合并为只读快照。
    for metadata_key in (
        "research_evidence_ownership",
        "research_evidence_dependencies",
    ):
        if metadata_key in derived:
            state[metadata_key] = derived[metadata_key]
    evidence = list(derived.get("research_evidence") or [])
    report = state.get("report") or {}

    error_codes = _collect_error_codes(run, state)
    hit_error_code = next(
        (
            code
            for code in (case.expected.get("allowed_error_codes") or [])
            if code in error_codes
        ),
        None,
    )
    explicit_rejection = _is_explicit_rejection(
        run,
        state,
        case.expected,
        hit_error_code,
    )

    checks: list[dict[str, Any]] = []
    if not harness_error:
        checks.extend(
            check_case_assertions(
                case,
                run,
                state,
                evidence,
                asset_refs,
                hit_error_code,
                explicit_rejection,
                usage,
            )
        )
        if not explicit_rejection:
            # 只有真实拒绝时研究循环未启动，证据模式要求才不适用。
            checks.extend(check_required_patterns(case, evidence, state, asset_refs))
        checks.append(_check_case_budget(case, usage))
        checks.extend(check_global_invariants(state, evidence, run.get("id")))

    violations = [
        item["name"] + (f" [{item.get('detail', '')}]" if item.get("detail") else "")
        for item in checks
        if not item.get("ok")
    ]
    outcome = classify_outcome(state, checks, harness_error, explicit_rejection)

    created_at = run.get("created_at")
    updated_at = run.get("updated_at")
    duration_s = (
        round((updated_at - created_at).total_seconds(), 1)
        if created_at is not None and updated_at is not None
        else None
    )
    remaining = state.get("remaining_budget") or {}

    unified = {
        "case_id": case.id,
        "name": case.name,
        "categories": case.categories,
        "question": case.question,
        "outcome": outcome,
        "violations": violations,
        "engine": "legacy",
        "run": {
            "id": run.get("id"),
            "record_id": run.get("record_id"),
            "status": run.get("status"),
            "execution_mode": run.get("execution_mode"),
            "error_class": run.get("error_class"),
            "error": run.get("error"),
            "matched_routing_error_code": hit_error_code,
            "error_codes": sorted(error_codes),
            "rejection_stage": _rejection_stage(run, state) if hit_error_code else None,
            "duration_s": duration_s,
            "config": run.get("config") or {},
            "schema_version": version_snapshot.get("schema_version"),
            "contract_version": version_snapshot.get("contract_version"),
            "schema_fingerprint": version_snapshot.get("schema_fingerprint"),
            "scope_fingerprint": version_snapshot.get("scope_fingerprint"),
        },
        "research": {
            "status": state.get("status"),
            "finish_reason": state.get("finish_reason"),
            "premise_supported": state.get("premise_supported"),
            "iterations": len(state.get("iteration_records") or ()),
            "evidence_count": len(evidence),
            "hypotheses": state.get("hypotheses"),
            "covered_dimension_refs": state.get("covered_dimension_refs"),
            "covered_driver_metric_refs": state.get("covered_driver_metric_refs"),
            "covered_hierarchy_ids": state.get("covered_hierarchy_ids"),
            "covered_contribution_dimension_refs": state.get(
                "covered_contribution_dimension_refs"
            ),
            "failed_action_error_codes": [
                failure.get("error_code")
                for record_ in state.get("iteration_records") or ()
                for failure in (record_.get("failed_actions") or ())
            ],
        },
        "runtime": {
            "remaining_budget": remaining,
            **usage,
        },
        "report_summary": report.get("summary"),
        "findings": [
            {
                "statement": item.get("statement"),
                "evidence_ids": list(item.get("evidence_ids") or ()),
                "claim_level": item.get("claim_level"),
            }
            for item in report.get("findings") or ()
        ],
        "limitations": report.get("limitations"),
        "evidence_digest": [
            {
                "evidence_id": item.get("evidence_id"),
                "purpose": item.get("purpose"),
                "metric_refs": list(item.get("metric_refs") or ()),
                "dimension_refs": list(item.get("dimension_refs") or ()),
                "time_roles": list(item.get("time_roles") or ()),
                "logical_columns": list(item.get("logical_columns") or ()),
                "top_rows": list(item.get("top_rows") or ()),
                "bottom_rows": list(item.get("bottom_rows") or ()),
                "applied_filters": list(item.get("applied_filters") or ()),
                "lineage": item.get("lineage"),
                "row_count": (item.get("statistics") or {}).get("row_count"),
                "truncated": (item.get("statistics") or {}).get("truncated"),
            }
            for item in evidence
        ],
        "checks": checks,
    }
    print(f"[case:{case.id}] outcome={outcome}" + (f" violations={violations}" if violations else ""))
    return CaseRecord(
        case_id=case.id,
        name=case.name,
        outcome=outcome,
        violations=violations,
        checks=checks,
        record=unified,
    )


def build_aggregate(records: list[CaseRecord], elapsed_s: float) -> dict[str, Any]:
    outcomes = [item.outcome for item in records]
    total = len(records)

    def count(outcome: str) -> int:
        return outcomes.count(outcome)

    invariant_failures: dict[str, int] = {}
    case_failure_tally: dict[str, int] = {}
    error_code_distribution: dict[str, int] = {}
    for item in records:
        for violation in item.violations:
            key = violation.split("[", 1)[0].strip()
            if key.startswith("invariant:"):
                invariant_failures[key] = invariant_failures.get(key, 0) + 1
            else:
                case_failure_tally[key] = case_failure_tally.get(key, 0) + 1
        for code in item.record.get("research", {}).get("failed_action_error_codes") or []:
            error_code_distribution[code] = error_code_distribution.get(code, 0) + 1

    durations = [
        item.record["run"]["duration_s"]
        for item in records
        if item.record["run"].get("duration_s") is not None
    ]
    model_calls = [
        item.record["runtime"]["model_calls"]["value"]
        for item in records
        if isinstance(item.record["runtime"].get("model_calls", {}).get("value"), int)
    ]
    queries = [
        item.record["runtime"]["queries"]["value"]
        for item in records
        if isinstance(item.record["runtime"].get("queries", {}).get("value"), int)
    ]

    return {
        "total_cases": total,
        "pass": count("pass"),
        "correct_reject": count("correct_reject"),
        "explicit_failure": count("explicit_failure"),
        "silent_error": count("silent_error"),
        "harness_error": count("harness_error"),
        "rates": {
            "pass_rate": round(count("pass") / total, 4) if total else None,
            "correct_reject_rate": round(count("correct_reject") / total, 4) if total else None,
            "explicit_failure_rate": round(count("explicit_failure") / total, 4) if total else None,
            "silent_error_rate": round(count("silent_error") / total, 4) if total else None,
        },
        "hard_invariant_failures": invariant_failures,
        "case_check_failure_distribution": case_failure_tally,
        "action_error_code_distribution": error_code_distribution,
        "runtime_summary": {
            "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
            "max_duration_s": max(durations) if durations else None,
            "avg_model_calls_observed": (
                round(sum(model_calls) / len(model_calls), 1) if model_calls else None
            ),
            "avg_queries_observed": (
                round(sum(queries) / len(queries), 1) if queries else None
            ),
            "model_calls_observed_cases": len(model_calls),
            "queries_observed_cases": len(queries),
            "eval_wall_time_s": round(elapsed_s, 1),
        },
    }


def _git_metadata() -> dict[str, Any]:
    """记录代码版本和工作树状态；命令失败时保留明确不可用状态。"""

    def run_git(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=BACKEND_ROOT.parent,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip()

    return {
        "commit": run_git("rev-parse", "HEAD"),
        "dirty": bool(run_git("status", "--porcelain", "--untracked-files=no")),
    }


def _eval_meta(
    payload: dict[str, Any],
    cases_file: Path,
    args: argparse.Namespace,
    records: list[CaseRecord],
    started: datetime,
) -> dict[str, Any]:
    cases_bytes = cases_file.read_bytes()
    defaults = payload.get("defaults") or {}
    first_config = next(
        (
            record.record.get("run", {}).get("config") or {}
            for record in records
            if record.record.get("run", {}).get("config")
        ),
        {},
    )
    first_run = next(
        (
            record.record.get("run", {})
            for record in records
            if record.record.get("run", {}).get("schema_fingerprint")
            or record.record.get("run", {}).get("scope_fingerprint")
        ),
        {},
    )
    schema_fingerprint = first_run.get("schema_fingerprint")
    scope_fingerprint = first_run.get("scope_fingerprint")
    try:
        cases_path = cases_file.resolve().relative_to(BACKEND_ROOT.parent.resolve())
        cases_location = str(cases_path)
    except ValueError:
        cases_location = cases_file.name
    model_keys = {
        key: value
        for key, value in first_config.items()
        if str(key).lower() in {"model", "model_name", "provider", "temperature"}
    }
    model_unavailable_reason = (
        None if model_keys else "run_config_model_fields_missing"
    )
    schema_fingerprint_unavailable_reason = (
        None if schema_fingerprint else "run_version_snapshot_missing"
    )
    scope_fingerprint_unavailable_reason = (
        None if scope_fingerprint else "run_version_snapshot_missing"
    )
    return {
        "engine": "legacy",
        "observability_contract_version": OBSERVABILITY_CONTRACT_VERSION,
        "ran_at": started.isoformat(timespec="seconds"),
        "tenant_id": args.tenant_id or defaults.get("tenant_id"),
        "user_id": args.user_id or defaults.get("user_id"),
        "dataset_id": args.dataset_id or defaults.get("dataset_id"),
        "datasource_id": args.datasource_id or defaults.get("datasource_id"),
        "model": model_keys or None,
        "model_unavailable_reason": model_unavailable_reason,
        "cases_file": cases_location,
        "cases_file_version": payload.get("version"),
        "cases_file_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "schema_version": first_run.get("schema_version") or payload.get("schema_version"),
        "contract_version": first_run.get("contract_version") or payload.get("contract_version"),
        "schema_fingerprint": schema_fingerprint,
        "schema_fingerprint_unavailable_reason": schema_fingerprint_unavailable_reason,
        "scope_fingerprint": scope_fingerprint,
        "scope_fingerprint_unavailable_reason": scope_fingerprint_unavailable_reason,
        "covered_categories": sorted(payload.get("covered_categories") or ()),
        "uncovered_categories": sorted(
            str(category) for category in (payload.get("uncovered_categories") or {})
        ),
        "code": _git_metadata(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--datasource-id", type=int, default=None)
    parser.add_argument("--dataset-id", type=int, default=None)
    parser.add_argument("--cases-file", type=Path, default=CASES_FILE)
    parser.add_argument("--case", action="append", default=[], help="按用例 ID 选择，可重复")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULTS_FILE,
    )
    args = parser.parse_args()

    payload, cases = load_cases(args.cases_file)
    if args.list_cases:
        for case in cases:
            print(f"{case.id} [{','.join(map(str, case.categories))}] {case.name}: {case.question}")
        return 0

    selected = set(args.case)
    cases = [case for case in cases if not selected or case.id in selected]
    if not cases:
        raise SystemExit(f"没有选中的用例: {selected}")

    started = datetime.now()
    records: list[CaseRecord] = []
    for case in cases:
        records.append(evaluate_case(case, args, payload.get("defaults") or {}, payload.get("asset_refs") or {}))
    elapsed = (datetime.now() - started).total_seconds()

    result = {
        "eval_meta": _eval_meta(payload, args.cases_file, args, records, started),
        "aggregate": build_aggregate(records, elapsed),
        "records": [item.record for item in records],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    aggregate = result["aggregate"]
    print(f"\n{'=' * 88}\n基线汇总: {aggregate['pass'] + aggregate['correct_reject']}/{aggregate['total_cases']} pass/correct-reject")
    print(
        f"  explicit_failure={aggregate['explicit_failure']} "
        f"silent_error={aggregate['silent_error']} harness_error={aggregate['harness_error']}"
    )
    for name, tally in aggregate["case_check_failure_distribution"].items():
        print(f"  [case-check] {name}: {tally}")
    for name, tally in aggregate["hard_invariant_failures"].items():
        print(f"  [invariant] {name}: {tally}")
    print(f"明细已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
