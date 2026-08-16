from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.chatbi.adapters.question_model import (  # noqa: E402
    LangChainQuestionModelClient,
)
from apps.chatbi.models import QuestionModelResponse  # noqa: E402
from apps.chatbi.services.understanding import (  # noqa: E402
    QuestionUnderstandingService,
)
from apps.semantic.composition import build_semantic_schema_service  # noqa: E402
from apps.temporal import build_temporal_context  # noqa: E402
from common.core.db import engine  # noqa: E402


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    purpose: str
    expected: dict[str, Any]


CASES = (
    EvaluationCase(
        case_id="metric_filter",
        question="今天店铺ID 100011的活跃客户数-线上访问是多少？",
        purpose="验证指标、相对时间和单值筛选维度。",
        expected={
            "rewritten_question": "今天店铺ID 100011的活跃客户数-线上访问是多少？",
            "intent_type": "metric_query",
            "metric_mentions_contains": "活跃客户数-线上访问",
            "time_raw": "今天",
            "normalized_time": {"start": "2026-08-12", "end_exclusive": "2026-08-13"},
            "residual_filter_mentions": [],
            "dimension_role": {"name_contains": "店铺ID", "role": "filter"},
            "dimension_value_contains": "100011",
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": False,
                "needs_order_by": False,
                "limit": None,
                "time_grain": None,
            },
            "validation_status": "valid",
        },
    ),
    EvaluationCase(
        case_id="grouped_metric",
        question="最近7天各店铺ID的新增客户数-线上是多少？",
        purpose="验证明确分组维度和相对时间范围。",
        expected={
            "rewritten_question": "最近7天各店铺ID的新增客户数-线上是多少？",
            "intent_type": "metric_query",
            "metric_mentions_contains": "新增客户数-线上",
            "time_raw": "最近7天",
            "normalized_time": {"start": "2026-08-06", "end_exclusive": "2026-08-13"},
            "residual_filter_mentions": [],
            "dimension_role": {"name_contains": "店铺ID", "role": "group_by"},
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
                "needs_order_by": False,
                "limit": None,
                "time_grain": None,
            },
            "validation_status": "valid",
        },
    ),
    EvaluationCase(
        case_id="top_ranking",
        question="本月新增客户数-线上最高的5个店铺ID是哪些？",
        purpose="验证排名、降序和明确 TopN 数量由模型生成。",
        expected={
            "rewritten_question": "本月新增客户数-线上最高的5个店铺ID是哪些？",
            "intent_type": "ranking_analysis",
            "metric_mentions_contains": "新增客户数-线上",
            "time_raw": "本月",
            "normalized_time": {"start": "2026-08-01", "end_exclusive": "2026-09-01"},
            "residual_filter_mentions": [],
            "dimension_role": {"name_contains": "店铺ID", "role": "group_by"},
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
                "needs_order_by": True,
                "order_direction": "desc",
                "limit": 5,
                "time_grain": None,
            },
            "validation_status": "valid",
        },
    ),
    EvaluationCase(
        case_id="bottom_ranking",
        question="本月新增客户数-线上最低的3个店铺ID是哪些？",
        purpose="验证低值排名需要升序，防止方向识别错误。",
        expected={
            "rewritten_question": "本月新增客户数-线上最低的3个店铺ID是哪些？",
            "intent_type": "ranking_analysis",
            "metric_mentions_contains": "新增客户数-线上",
            "time_raw": "本月",
            "normalized_time": {"start": "2026-08-01", "end_exclusive": "2026-09-01"},
            "residual_filter_mentions": [],
            "dimension_role": {"name_contains": "店铺ID", "role": "group_by"},
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
                "needs_order_by": True,
                "order_direction": "asc",
                "limit": 3,
                "time_grain": None,
            },
            "validation_status": "valid",
        },
    ),
    EvaluationCase(
        case_id="daily_trend",
        question="查看最近7天每天的活跃客户数-线上访问趋势。",
        purpose="验证趋势意图、按天粒度和时间范围。",
        expected={
            "rewritten_question": "查看最近7天每天的活跃客户数-线上访问趋势。",
            "intent_type": "trend_analysis",
            "metric_mentions_contains": "活跃客户数-线上访问",
            "time_raw": "最近7天",
            "normalized_time": {"start": "2026-08-06", "end_exclusive": "2026-08-13"},
            "residual_filter_mentions": [],
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": True,
                "needs_order_by": False,
                "limit": None,
                "time_grain": "day",
            },
            "validation_status": "valid",
        },
    ),
    EvaluationCase(
        case_id="ambiguous_dimension",
        question="最近7天店铺ID的新增客户数-线上是多少？",
        purpose="验证维度用途不明确时不会被服务端擅自改成分组或筛选。",
        expected={
            "rewritten_question": "最近7天店铺ID的新增客户数-线上是多少？",
            "intent_type": "metric_query",
            "metric_mentions_contains": "新增客户数-线上",
            "time_raw": "最近7天",
            "normalized_time": {"start": "2026-08-06", "end_exclusive": "2026-08-13"},
            "residual_filter_mentions": [],
            "dimension_role": {"name_contains": "店铺ID", "role": "ambiguous"},
            "query_shape": {
                "select_mode": "aggregate",
                "needs_group_by": False,
                "needs_order_by": False,
                "limit": None,
                "time_grain": None,
            },
            "validation_status": "clarification_required",
            "reason_codes_contains": "dimension_role_ambiguous",
        },
    ),
)


class RecordingQuestionModelClient:
    """调用真实模型，并保留每个问题理解阶段的完整输入输出。"""

    def __init__(self) -> None:
        self._client = LangChainQuestionModelClient()
        self.calls: list[dict[str, Any]] = []
        self._calls_lock = Lock()

    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionModelResponse:
        stage = _stage_from_prompt(system_prompt)
        started_at = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
        response = self._client.invoke(system_prompt, user_prompt)
        finished_at = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
        call = {
            "stage": stage,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": round(
                (finished_at - started_at).total_seconds() * 1000,
                2,
            ),
            "input": _parse_json_or_text(user_prompt),
            "raw_output": response.content,
            "parsed_output": _parse_json_or_text(response.content),
            "usage_metadata": response.usage_metadata,
        }
        with self._calls_lock:
            call["sequence"] = len(self.calls) + 1
            self.calls.append(call)
        return response


def _stage_from_prompt(system_prompt: str) -> str:
    if "重写器" in system_prompt:
        return "QUESTION_REWRITE"
    if "统一问题理解器" in system_prompt:
        return "QUESTION_UNDERSTANDING"
    if "意图识别器" in system_prompt:
        return "INTENT_RECOGNITION"
    if "维度槽位识别器" in system_prompt:
        return "DIMENSION_RECOGNITION"
    if "时间" in system_prompt:
        return "TEMPORAL_INTERPRETATION"
    return "UNKNOWN"


def _parse_json_or_text(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _contains(value: Any, expected: str) -> bool:
    if isinstance(value, list):
        return any(expected in str(item) for item in value)
    return expected in str(value)


def _evaluate(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    intent = actual["intent"]
    validation = actual["validation"]
    checks: list[dict[str, Any]] = []

    def add(field: str, expected_value: Any, actual_value: Any, passed: bool) -> None:
        checks.append(
            {
                "field": field,
                "expected": expected_value,
                "actual": actual_value,
                "passed": passed,
            }
        )

    add(
        "rewritten_question",
        expected["rewritten_question"],
        actual["rewritten_question"],
        actual["rewritten_question"] == expected["rewritten_question"],
    )

    add(
        "intent.intent_type",
        expected["intent_type"],
        intent["intent_type"],
        intent["intent_type"] == expected["intent_type"],
    )
    metric = expected.get("metric_mentions_contains")
    if metric is not None:
        add(
            "intent.metric_mentions",
            f"包含 {metric}",
            intent["metric_mentions"],
            _contains(intent["metric_mentions"], metric),
        )
    add(
        "intent.time_range.raw",
        expected["time_raw"],
        intent["time_range"]["raw"],
        intent["time_range"]["raw"] == expected["time_raw"],
    )
    normalized_time = intent["time_range"].get("normalized") or {}
    for key, expected_value in expected["normalized_time"].items():
        add(
            f"intent.time_range.normalized.{key}",
            expected_value,
            normalized_time.get(key),
            normalized_time.get(key) == expected_value,
        )
    add(
        "intent.filter_mentions",
        expected["residual_filter_mentions"],
        intent["filter_mentions"],
        intent["filter_mentions"] == expected["residual_filter_mentions"],
    )
    expected_dimension = expected.get("dimension_role")
    if expected_dimension is not None:
        matching_slots = [
            slot
            for slot in intent["dimension_slots"]
            if expected_dimension["name_contains"] in str(slot.get("name"))
        ]
        actual_roles = [slot.get("role") for slot in matching_slots]
        add(
            "intent.dimension_slots.role",
            expected_dimension,
            matching_slots,
            expected_dimension["role"] in actual_roles,
        )
    dimension_value = expected.get("dimension_value_contains")
    if dimension_value is not None:
        actual_values = [slot.get("value") for slot in intent["dimension_slots"]]
        add(
            "intent.dimension_slots.value",
            f"包含 {dimension_value}",
            actual_values,
            _contains(actual_values, dimension_value),
        )
    for key, expected_value in expected["query_shape"].items():
        actual_value = intent["query_shape"].get(key)
        add(
            f"intent.query_shape.{key}",
            expected_value,
            actual_value,
            actual_value == expected_value,
        )
    add(
        "validation.status",
        expected["validation_status"],
        validation["status"],
        validation["status"] == expected["validation_status"],
    )
    reason_code = expected.get("reason_codes_contains")
    if reason_code is not None:
        add(
            "validation.reason_codes",
            f"包含 {reason_code}",
            validation["reason_codes"],
            reason_code in validation["reason_codes"],
        )
    return checks


def run_case(
    case: EvaluationCase,
    *,
    tenant_id: int,
    dataset_id: int,
    datasource_id: int | None,
) -> dict[str, Any]:
    recorder = RecordingQuestionModelClient()
    reference_at = datetime(2026, 8, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    with Session(engine) as session:
        service = QuestionUnderstandingService(
            model_client=recorder,
            schema_provider=build_semantic_schema_service(session),
        )
        started_at = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
        try:
            outcome = service.understand(
                question=case.question,
                datasource_id=datasource_id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                temporal_context=build_temporal_context(reference_at=reference_at),
            )
            actual = outcome.output.model_dump(mode="json")
            checks = _evaluate(case.expected, actual)
            error = None
            usage = outcome.usage_metadata
        except Exception as exc:
            actual = None
            checks = []
            error = {"type": type(exc).__name__, "message": str(exc)}
            usage = {}
        finished_at = datetime.now(tz=ZoneInfo("Asia/Shanghai"))

    passed_count = sum(check["passed"] for check in checks)
    return {
        "case_id": case.case_id,
        "question": case.question,
        "purpose": case.purpose,
        "expected": case.expected,
        "fixed_temporal_context": {
            "reference_at": reference_at.isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "model_calls": recorder.calls,
        "final_understanding": actual,
        "validation_effect": (
            {
                "status": actual["validation"]["status"],
                "reason_codes": actual["validation"]["reason_codes"],
                "clarification_slots": actual["validation"]["clarification_slots"],
                "explanation": (
                    "校验阻止当前理解直接进入后续执行。"
                    if actual["validation"]["status"] != "valid"
                    else "校验未发现阻断问题，当前理解可以进入后续流程。"
                ),
            }
            if actual is not None
            else None
        ),
        "accuracy_checks": checks,
        "score": {
            "passed": passed_count,
            "total": len(checks),
            "accuracy": round(passed_count / len(checks), 4) if checks else 0.0,
        },
        "usage_metadata": usage,
        "error": error,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": round((finished_at - started_at).total_seconds() * 1000, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="使用真实模型评估 Agent 问题理解与校验流程。")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--dataset-id", type=int, default=3)
    parser.add_argument("--datasource-id", type=int, default=None)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    selected_cases = [
        case for case in CASES if not args.case_ids or case.case_id in args.case_ids
    ]
    unknown_cases = set(args.case_ids or ()) - {case.case_id for case in CASES}
    if unknown_cases:
        parser.error(f"未知测试用例: {', '.join(sorted(unknown_cases))}")

    results = [
        run_case(
            case,
            tenant_id=args.tenant_id,
            dataset_id=args.dataset_id,
            datasource_id=args.datasource_id,
        )
        for case in selected_cases
    ]
    total_checks = sum(item["score"]["total"] for item in results)
    passed_checks = sum(item["score"]["passed"] for item in results)
    report = {
        "test_type": "real_model_question_understanding_evaluation",
        "generated_at": datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(),
        "environment": {
            "tenant_id": args.tenant_id,
            "dataset_id": args.dataset_id,
            "datasource_id": args.datasource_id,
            "fixed_reference_at": "2026-08-12T10:00:00+08:00",
        },
        "summary": {
            "case_count": len(results),
            "successful_case_count": sum(item["error"] is None for item in results),
            "passed_checks": passed_checks,
            "total_checks": total_checks,
            "field_accuracy": (
                round(passed_checks / total_checks, 4) if total_checks else 0.0
            ),
        },
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"完整记录: {args.output.resolve()}")
    return 0 if all(item["error"] is None for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
