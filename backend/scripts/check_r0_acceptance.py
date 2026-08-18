"""检查 R0 观测冻结门禁并生成可审计报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_UNDERSTANDING_MARKERS = (
    "QUESTION_UNDERSTANDING",
    "QUESTION_REWRITE",
    "TEMPORAL_",
    "QUESTION_MODEL",
)
_INVARIANT_MARKER = "INVARIANT_VIOLATION"
_STAGE_NAMES = {"understanding", "binding", "plan"}


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _node_details(run: dict[str, Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    fixtures = run.get("stage_fixtures")
    if not isinstance(fixtures, dict):
        return details
    for stage in _STAGE_NAMES:
        fixture = fixtures.get(stage)
        if not isinstance(fixture, dict):
            continue
        nodes = fixture.get("nodes")
        if isinstance(nodes, list):
            details.extend(item for item in nodes if isinstance(item, dict))
    return details


def _failure_stage(run: dict[str, Any]) -> str | None:
    """按 Trace 节点和稳定错误码归因，不能只按终态猜测。"""

    for detail in reversed(_node_details(run)):
        node = detail.get("node")
        if not isinstance(node, dict):
            continue
        status = str(node.get("status") or "")
        if status not in {"failed", "rejected", "interrupted"}:
            continue
        name = str(node.get("name") or "")
        if name in {"question_understanding", "question_rewrite", "temporal_processing"}:
            return "understanding"
        if name in {"semantic_retrieval", "semantic_binding"}:
            return "binding"
        if name in {
            "analysis_plan_snapshot",
            "semantic_compilation",
            "sql_validation",
            "sql_execution",
        }:
            return "plan"
    error_text = _flatten_text(
        {
            "error_class": run.get("error_class"),
            "failure_message": run.get("failure_message"),
        }
    ).upper()
    if any(marker in error_text for marker in _UNDERSTANDING_MARKERS):
        return "understanding"
    if "RETRIEVAL" in error_text or "BINDING" in error_text:
        return "binding"
    if "PLAN" in error_text or "SQL" in error_text:
        return "plan"
    return None


def evaluate_run(run: dict[str, Any]) -> dict[str, Any]:
    """计算单个 Run 的 R0 结构检查结果。"""

    # 只读取终态错误字段，不能扫描完整 Trace；完整 Trace 必然包含
    # QUESTION_UNDERSTANDING 节点名，会把计划失败误判为理解失败。
    failure_text = _flatten_text(
        {
            "error_class": run.get("error_class"),
            "failure_message": run.get("failure_message"),
        }
    ).upper()
    status = str(run.get("status") or "")
    understanding_failed = status == "failed" and any(
        marker in failure_text for marker in _UNDERSTANDING_MARKERS
    )
    invariant_violation = _INVARIANT_MARKER in failure_text
    # 时间/比较语义丢失只能由明确错误或契约检查标记判定，不能把普通失败误报为语义丢失。
    time_comparison_loss = any(
        marker in failure_text
        for marker in (
            "TEMPORAL_SEMANTIC_LOST",
            "TIME_COMPARISON_LOST",
            "TEMPORAL_AUTHORITY_LOST",
        )
    )
    attribution = _failure_stage(run)
    failed = status in {"failed", "cancelled"} or bool(run.get("failure_message"))
    fixtures = run.get("stage_fixtures")
    required_stages = {"understanding"}
    if status == "finished":
        required_stages = _STAGE_NAMES
    fixture_complete = isinstance(fixtures, dict) and all(
        isinstance(fixtures.get(stage), dict) and bool(fixtures[stage].get("complete"))
        for stage in required_stages
    )
    return {
        "case_id": run.get("case_id"),
        "run_id": run.get("run_id"),
        "understanding_failed": understanding_failed,
        "invariant_violation": invariant_violation,
        "time_comparison_loss": time_comparison_loss,
        "failure_attributed": not failed or attribution is not None,
        "failure_stage": attribution,
        "stage_fixtures_complete": fixture_complete,
        "trace_available": bool(run.get("trace_available")),
    }


def evaluate_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """汇总主问题和澄清分支，输出 R0 门禁结果。"""

    runs: list[dict[str, Any]] = []
    for case in summary.get("cases") or []:
        if not isinstance(case, dict):
            continue
        primary = case.get("primary")
        if isinstance(primary, dict):
            runs.append(primary)
        branches = case.get("clarification_branches")
        if isinstance(branches, list):
            runs.extend(item for item in branches if isinstance(item, dict))
    checks = [evaluate_run(run) for run in runs]
    report = {
        "run_count": len(checks),
        "understanding_failed": sum(item["understanding_failed"] for item in checks),
        "invariant_violations": sum(item["invariant_violation"] for item in checks),
        "time_comparison_loss": sum(item["time_comparison_loss"] for item in checks),
        "unattributed_failures": sum(
            not item["failure_attributed"] for item in checks
        ),
        "missing_trace": sum(not item["trace_available"] for item in checks),
        "incomplete_stage_fixtures": sum(
            not item["stage_fixtures_complete"] for item in checks
        ),
        "checks": checks,
    }
    report["passed"] = all(
        report[key] == 0
        for key in (
            "understanding_failed",
            "invariant_violations",
            "time_comparison_loss",
            "unattributed_failures",
            "missing_trace",
            "incomplete_stage_fixtures",
        )
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 ChatBI R0 观测冻结门禁")
    parser.add_argument("results", type=Path, help="真实跑批 JSON 结果文件")
    parser.add_argument("--report", type=Path, help="可选的报告输出文件")
    args = parser.parse_args()
    summary = json.loads(args.results.read_text(encoding="utf-8"))
    report = evaluate_summary(summary)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
