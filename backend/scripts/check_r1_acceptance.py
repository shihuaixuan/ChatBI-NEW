"""检查 R1 理解层与检索层的结构门禁。

该脚本只验证契约结构，不把“有答案”当作 R1 通过。业务数值门禁仍由
P1 跑批报告单独负责。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "p1_golden_cases.jsonl"


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    """递归查找 trace 详情中的结构化对象。"""

    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _load_cases(path: Path = CASES_PATH) -> dict[str, dict[str, Any]]:
    """加载黄金题，复用结构校验脚本，避免两套题集口径漂移。"""

    from scripts.validate_p1_golden_cases import load_and_validate_cases

    schema_path = path.with_suffix(".schema.json")
    return {
        case["case_id"]: case
        for case in load_and_validate_cases(path, schema_path)
    }


def _stage_details(run: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    fixture = (run.get("stage_fixtures") or {}).get(stage)
    if not isinstance(fixture, dict):
        return []
    return [item for item in fixture.get("nodes") or [] if isinstance(item, dict)]


def _question_understanding(run: dict[str, Any]) -> dict[str, Any] | None:
    """取最后一个完整的问题理解详情，避免把重试前的失败快照当成事实。"""

    candidates: list[dict[str, Any]] = []
    for detail in _stage_details(run, "understanding"):
        candidates.extend(
            item
            for item in _walk_dicts(detail.get("output_detail"))
            if isinstance(item.get("question_understanding"), dict)
        )
    for candidate in reversed(candidates):
        output = candidate.get("question_understanding")
        if isinstance(output, dict):
            return output
    return None


def _mention_graph(output: dict[str, Any]) -> dict[str, Any] | None:
    graph = output.get("mention_graph")
    return graph if isinstance(graph, dict) else None


def _metric_mentions(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in graph.get("mentions") or []
        if isinstance(item, dict) and item.get("kind") == "metric_phrase"
    ]


def _actual_base_metric_texts(graph: dict[str, Any]) -> list[str]:
    """computed 是表达式，不属于指标检索资产，结构断言也不计入基础指标集。"""

    return [
        str(item.get("text") or "")
        for item in _metric_mentions(graph)
        if item.get("metric_role") != "computed"
    ]


def _check_spans(graph: dict[str, Any], rewritten_question: str) -> list[str]:
    errors: list[str] = []
    occupied: list[tuple[int, int, str]] = []
    for item in graph.get("mentions") or []:
        if not isinstance(item, dict):
            errors.append("MENTION_NOT_OBJECT")
            continue
        text = str(item.get("text") or "")
        start = item.get("start_offset")
        end = item.get("end_offset")
        mention_id = str(item.get("mention_id") or "")
        if not text or not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"MENTION_SPAN_INVALID:{mention_id}")
            continue
        if not (0 <= start < end <= len(rewritten_question)):
            errors.append(f"MENTION_SPAN_RANGE:{mention_id}")
        elif rewritten_question[start:end] != text:
            errors.append(f"MENTION_SPAN_TEXT_MISMATCH:{mention_id}")
        for old_start, old_end, old_id in occupied:
            if not (end <= old_start or start >= old_end):
                attached_to = str(item.get("attached_to") or "")
                if not (
                    item.get("kind") == "filter_value"
                    and attached_to == old_id
                ):
                    errors.append(f"MENTION_OVERLAP:{mention_id}:{old_id}")
        occupied.append((start, end, mention_id))
    return errors


def evaluate_understanding(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """执行一条黄金题的 R1 理解层结构断言。"""

    expected = case["understanding_expect"]
    output = _question_understanding(run)
    errors: list[str] = []
    if output is None:
        return {"passed": False, "errors": ["UNDERSTANDING_DETAIL_MISSING"]}
    graph = _mention_graph(output)
    if graph is None:
        return {"passed": False, "errors": ["MENTION_GRAPH_MISSING"]}

    rewritten = str(output.get("rewritten_question") or run.get("question") or "")
    errors.extend(_check_spans(graph, rewritten))

    actual_metrics = _actual_base_metric_texts(graph)
    if actual_metrics != list(expected.get("metric_phrases") or []):
        errors.append(f"METRIC_PHRASES:{actual_metrics!r}")

    actual_ops = [
        str(item.get("op") or "")
        for item in graph.get("expressions") or []
        if isinstance(item, dict)
    ]
    if actual_ops != list(expected.get("expression_ops") or []):
        errors.append(f"EXPRESSION_OPS:{actual_ops!r}")

    actual_time_count = sum(
        1
        for item in graph.get("mentions") or []
        if isinstance(item, dict) and item.get("kind") == "time_expression"
    )
    if actual_time_count != int(expected.get("time_range_count", 0)):
        errors.append(f"TIME_MENTION_COUNT:{actual_time_count}")

    expected_grain = expected.get("time_grain")
    actual_grain = (graph.get("query_shape") or {}).get("time_grain")
    if expected_grain is not None and actual_grain != expected_grain:
        errors.append(f"TIME_GRAIN:{actual_grain!r}")

    expected_decomposition = expected.get("decomposition")
    if isinstance(expected_decomposition, dict):
        matching = [
            item
            for item in _metric_mentions(graph)
            if item.get("text") == expected["metric_phrases"][0]
        ]
        decomposition = matching[0].get("decomposition") if matching else None
        if not isinstance(decomposition, dict):
            errors.append("DECOMPOSITION_MISSING")
        else:
            expected_numerator = expected_decomposition.get(
                "numerator_text",
                expected_decomposition.get("numerator_metric_biz_name"),
            )
            expected_denominator = expected_decomposition.get(
                "denominator_text",
                expected_decomposition.get("denominator_metric_biz_name"),
            )
            if decomposition.get("numerator_text") != expected_numerator:
                errors.append("DECOMPOSITION_NUMERATOR_MISMATCH")
            if decomposition.get("denominator_text") != expected_denominator:
                errors.append("DECOMPOSITION_DENOMINATOR_MISMATCH")

    expected_conditions = expected.get("metric_conditions") or []
    actual_conditions = graph.get("metric_conditions") or []
    for condition in expected_conditions:
        if not any(
            isinstance(item, dict)
            and item.get("operator") == condition.get("operator")
            and item.get("value") == condition.get("value")
            for item in actual_conditions
        ):
            errors.append("METRIC_CONDITION_MISSING")

    expected_order = expected.get("order")
    if isinstance(expected_order, dict):
        actual_order = graph.get("order")
        refs = {
            str(item.get("mention_id")): str(item.get("text") or "")
            for item in graph.get("mentions") or []
            if isinstance(item, dict)
        }
        target = refs.get(str((actual_order or {}).get("ref")))
        if not isinstance(actual_order, dict):
            errors.append("ORDER_MISSING")
        elif actual_order.get("direction") != expected_order.get("direction"):
            errors.append("ORDER_DIRECTION_MISMATCH")
        elif target != expected_order.get("target_metric_phrase"):
            errors.append("ORDER_TARGET_MISMATCH")

    return {
        "passed": not errors,
        "errors": errors,
        "metric_phrases": actual_metrics,
        "expression_ops": actual_ops,
        "time_range_count": actual_time_count,
    }


def _retrieval_requests(run: dict[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for detail in _stage_details(run, "binding"):
        summary = detail.get("output_summary")
        if isinstance(summary, dict):
            request = summary.get("retrieval_request")
            if isinstance(request, dict):
                requests.append(request)
        for item in _walk_dicts(detail.get("input_detail")):
            request = item.get("semantic_retrieval_request")
            if isinstance(request, dict):
                requests.append(request)
        for item in _walk_dicts(detail.get("output_detail")):
            request = item.get("semantic_retrieval_request")
            if isinstance(request, dict):
                requests.append(request)
    # 同一请求可能在 input/output/state_diff 各出现一次，保留顺序去重。
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for request in requests:
        key = json.dumps(request, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(request)
    return result


def _retrieval_filters(run: dict[str, Any]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for detail in _stage_details(run, "binding"):
        summary = detail.get("output_summary")
        if isinstance(summary, dict):
            value = summary.get("retrieval_filters")
            if isinstance(value, dict):
                filters.append(value)
        for item in _walk_dicts(detail.get("output_detail")):
            value = item.get("semantic_retrieval_filters")
            if isinstance(value, dict):
                filters.append(value)
            result = item.get("result")
            if isinstance(result, dict):
                metadata = result.get("metadata")
                if isinstance(metadata, dict) and isinstance(
                    metadata.get("semantic_retrieval_filters"), dict
                ):
                    filters.append(metadata["semantic_retrieval_filters"])
    return filters


def _plan_subqueries(request: dict[str, Any]) -> list[dict[str, Any]]:
    """让验收消费真实 planner，而不是重新实现检索槽位规则。"""

    try:
        from apps.retrieval.models.dto import RetrievalRequest
        from apps.retrieval.projection.planner import SemanticBindingQueryPlanner

        model = RetrievalRequest.model_validate(request)
        return [item.model_dump(mode="json") for item in SemanticBindingQueryPlanner().plan(model).subqueries]
    except (TypeError, ValueError) as exc:
        return [{"_error": f"RETRIEVAL_REQUEST_INVALID:{exc}"}]


def evaluate_retrieval(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """执行一条黄金题的 R1 检索层结构断言。"""

    requests = _retrieval_requests(run)
    filters = _retrieval_filters(run)
    subqueries: list[dict[str, Any]] = []
    for request in requests:
        subqueries.extend(_plan_subqueries(request))
    for item in filters:
        raw = item.get("subqueries")
        if isinstance(raw, list):
            subqueries.extend(value for value in raw if isinstance(value, dict))

    errors: list[str] = []
    if not subqueries:
        errors.append("RETRIEVAL_TRACE_MISSING")
    if any("_error" in item for item in subqueries):
        errors.append("RETRIEVAL_TRACE_UNUSABLE")
    question = str(case.get("question") or run.get("question") or "")
    metric_slots = [item for item in subqueries if item.get("purpose") == "metric"]
    if any(str(item.get("text") or "") == question for item in metric_slots):
        errors.append("WHOLE_QUESTION_METRIC_RETRIEVAL")

    graph: dict[str, Any] | None = None
    understanding = _question_understanding(run)
    if understanding is not None:
        graph = _mention_graph(understanding)
    if graph is not None:
        computed = {
            str(item.get("text") or "")
            for item in _metric_mentions(graph)
            if item.get("metric_role") == "computed"
        }
        if any(str(item.get("text") or "") in computed for item in metric_slots):
            errors.append("COMPUTED_METRIC_SLOT")

    if case.get("case_id") == "P1-G007":
        ratio_ids = {
            str(item.get("subquery_id") or "")
            for item in subqueries
            if str(item.get("subquery_id") or "").startswith("ratio:")
        }
        if not any(item.endswith(":numerator") for item in ratio_ids):
            errors.append("RATIO_NUMERATOR_RETRIEVAL_MISSING")
        if not any(item.endswith(":denominator") for item in ratio_ids):
            errors.append("RATIO_DENOMINATOR_RETRIEVAL_MISSING")

    return {
        "passed": not errors,
        "errors": errors,
        "request_count": len(requests),
        "subquery_count": len(subqueries),
        "subqueries": subqueries,
    }


def evaluate_run(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    understanding = evaluate_understanding(case, run)
    retrieval = evaluate_retrieval(case, run)
    return {
        "case_id": case["case_id"],
        "run_id": run.get("run_id"),
        "understanding": understanding,
        "retrieval": retrieval,
        "passed": understanding["passed"] and retrieval["passed"],
    }


def evaluate_summary(summary: dict[str, Any], cases_path: Path = CASES_PATH) -> dict[str, Any]:
    """汇总 12 题结构门禁；缺失主 Run 也必须显式失败。"""

    cases = _load_cases(cases_path)
    run_by_case: dict[str, dict[str, Any]] = {}
    for item in summary.get("all_runs") or []:
        if isinstance(item, dict) and item.get("case_id"):
            run_by_case[str(item["case_id"])] = item
    # 兼容只保存 cases.primary 的历史结果文件。
    for item in summary.get("cases") or []:
        if not isinstance(item, dict):
            continue
        primary = item.get("primary")
        if isinstance(primary, dict) and primary.get("case_id"):
            run_by_case.setdefault(str(primary["case_id"]), primary)

    checks: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        run = run_by_case.get(case_id)
        if run is None:
            checks.append(
                {
                    "case_id": case_id,
                    "run_id": None,
                    "understanding": {"passed": False, "errors": ["RUN_MISSING"]},
                    "retrieval": {"passed": False, "errors": ["RUN_MISSING"]},
                    "passed": False,
                }
            )
        else:
            checks.append(evaluate_run(case, run))
    report = {
        "case_count": len(cases),
        "understanding_passed": sum(item["understanding"]["passed"] for item in checks),
        "retrieval_passed": sum(item["retrieval"]["passed"] for item in checks),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 ChatBI R1 结构门禁")
    parser.add_argument("results", type=Path, help="P1 跑批 JSON 结果文件")
    parser.add_argument("--report", type=Path, help="可选的报告输出文件")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    args = parser.parse_args()
    report = evaluate_summary(
        json.loads(args.results.read_text(encoding="utf-8")),
        args.cases,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
