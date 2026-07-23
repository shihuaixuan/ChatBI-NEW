"""统一检索 Gold Set 数据格式与离线评测。"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from statistics import fmean

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalPurpose,
    RetrievalRequest,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldenSlotExpectation(_StrictModel):
    """单个检索槽位的相关候选与预期决策。"""

    subquery_id: str = Field(min_length=1)
    purpose: RetrievalPurpose
    expected_status: RetrievalDecisionStatus
    relevant_assets: list[AssetReference] = Field(default_factory=list)
    expected_selected_assets: list[AssetReference] = Field(default_factory=list)
    forbidden_assets: list[AssetReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_asset_sets(self) -> GoldenSlotExpectation:
        relevant = {_asset_key(item) for item in self.relevant_assets}
        selected = {_asset_key(item) for item in self.expected_selected_assets}
        forbidden = {_asset_key(item) for item in self.forbidden_assets}
        if not selected.issubset(relevant):
            raise ValueError("预期选中资产必须属于相关资产")
        if relevant & forbidden:
            raise ValueError("同一资产不能同时标记为相关和禁止")
        if self.expected_status == RetrievalDecisionStatus.RESOLVED and not selected:
            raise ValueError("resolved 槽位必须提供预期选中资产")
        return self


class RetrievalGoldenCase(_StrictModel):
    """一条可被 Graph、Agent 和候选策略共同执行的评测样本。"""

    case_id: str = Field(min_length=1)
    request: RetrievalRequest
    expected_status: RetrievalDecisionStatus
    expected_allowed_assets: list[ExecutableAssetReference] = Field(default_factory=list)
    slots: list[GoldenSlotExpectation] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def validate_case(self) -> RetrievalGoldenCase:
        slot_ids = [item.subquery_id for item in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("Gold Set 中的 subquery_id 不允许重复")
        allowed = [_asset_key(item) for item in self.expected_allowed_assets]
        if len(allowed) != len(set(allowed)):
            raise ValueError("Gold Set 中的 expected_allowed_assets 不允许重复")
        selected_executable = {
            _asset_key(asset)
            for slot in self.slots
            for asset in slot.expected_selected_assets
            if asset.asset_type.value in {"METRIC", "DIMENSION"}
        }
        allowed_set = set(allowed)
        executable_statuses = {
            RetrievalDecisionStatus.RESOLVED,
            RetrievalDecisionStatus.CROSS_MODEL,
        }
        if self.expected_status in executable_statuses and allowed_set != selected_executable:
            raise ValueError("可执行 Gold Case 的白名单必须等于全部槽位选中资产")
        if self.expected_status not in executable_statuses and allowed_set:
            raise ValueError("不可执行 Gold Case 不能提供资产白名单")
        return self


class RecordedRetrievalResult(_StrictModel):
    """某个实现对一条 Gold Case 的实际输出。"""

    case_id: str = Field(min_length=1)
    bundle: RetrievalBundle


class RetrievalBaseline(_StrictModel):
    """Graph、Agent 或候选策略的一次完整基线快照。"""

    implementation: str = Field(min_length=1)
    captured_at: datetime
    strategy_version: str = Field(min_length=1)
    results: list[RecordedRetrievalResult]

    @model_validator(mode="after")
    def validate_unique_cases(self) -> RetrievalBaseline:
        case_ids = [item.case_id for item in self.results]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("同一基线中不能重复记录 case_id")
        return self


class CaseEvaluation(_StrictModel):
    """一条样本的评测明细。"""

    case_id: str
    result_present: bool
    status_match: bool = False
    slot_status_accuracy: float | None = None
    precision_at_1: float | None = None
    recall_at_k: float | None = None
    selected_assets_match: bool = False
    allowed_assets_match: bool = False
    wrong_auto_resolved: bool = False
    forbidden_asset_hits: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RetrievalEvaluationReport(_StrictModel):
    """一份实现基线的聚合评测结果。"""

    implementation: str
    captured_at: datetime
    strategy_version: str
    top_k: int
    case_count: int
    evaluated_case_count: int
    missing_result_count: int
    status_accuracy: float
    slot_status_accuracy: float | None = None
    precision_at_1: float | None = None
    recall_at_k: float | None = None
    selected_assets_accuracy: float
    allowed_assets_accuracy: float
    wrong_auto_resolved_rate: float
    security_violation_count: int
    channel_status_counts: dict[str, int]
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    cases: list[CaseEvaluation]


_GOLD_SET_ADAPTER = TypeAdapter(list[RetrievalGoldenCase])


def load_gold_set(path: Path) -> list[RetrievalGoldenCase]:
    """从 JSON 文件读取并严格校验 Gold Set。"""

    return _GOLD_SET_ADAPTER.validate_json(path.read_text(encoding="utf-8"))


def load_baseline(path: Path) -> RetrievalBaseline:
    """读取某个实现生成的基线快照。"""

    return RetrievalBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_baseline(
    gold_cases: list[RetrievalGoldenCase],
    baseline: RetrievalBaseline,
    *,
    top_k: int = 5,
) -> RetrievalEvaluationReport:
    """按槽位评估一份基线，保留缺失结果而不是静默跳过。"""

    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    actual_by_case = {item.case_id: item.bundle for item in baseline.results}
    evaluations = [_evaluate_case(case, actual_by_case.get(case.case_id), top_k=top_k) for case in gold_cases]
    evaluated = [item for item in evaluations if item.result_present]
    latencies = [actual_by_case[item.case_id].diagnostics.total_latency_ms for item in evaluated]
    channel_status_counts: dict[str, int] = {}
    for item in evaluated:
        for diagnostic in actual_by_case[item.case_id].diagnostics.channels:
            key = ":".join(
                (
                    diagnostic.channel.value,
                    diagnostic.status.value,
                    diagnostic.error_code or "none",
                )
            )
            channel_status_counts[key] = channel_status_counts.get(key, 0) + 1

    return RetrievalEvaluationReport(
        implementation=baseline.implementation,
        captured_at=baseline.captured_at,
        strategy_version=baseline.strategy_version,
        top_k=top_k,
        case_count=len(gold_cases),
        evaluated_case_count=len(evaluated),
        missing_result_count=len(gold_cases) - len(evaluated),
        status_accuracy=_mean_bool([item.status_match for item in evaluated]),
        slot_status_accuracy=_mean_optional([item.slot_status_accuracy for item in evaluated]),
        precision_at_1=_mean_optional([item.precision_at_1 for item in evaluated]),
        recall_at_k=_mean_optional([item.recall_at_k for item in evaluated]),
        selected_assets_accuracy=_mean_bool([item.selected_assets_match for item in evaluated]),
        allowed_assets_accuracy=_mean_bool([item.allowed_assets_match for item in evaluated]),
        wrong_auto_resolved_rate=_mean_bool([item.wrong_auto_resolved for item in evaluated]),
        security_violation_count=sum(len(item.forbidden_asset_hits) for item in evaluated),
        channel_status_counts=channel_status_counts,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        cases=evaluations,
    )


def report_json(report: RetrievalEvaluationReport) -> str:
    """生成稳定、便于 CI 保存的 JSON 报告。"""

    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _evaluate_case(
    case: RetrievalGoldenCase,
    bundle: RetrievalBundle | None,
    *,
    top_k: int,
) -> CaseEvaluation:
    if bundle is None:
        return CaseEvaluation(
            case_id=case.case_id,
            result_present=False,
            missing_slots=[item.subquery_id for item in case.slots],
            errors=["BASELINE_RESULT_MISSING"],
        )

    actual_slots = {item.subquery_id: item for item in bundle.decision.slot_decisions}
    slot_status_matches: list[bool] = []
    precision_values: list[float] = []
    recall_values: list[float] = []
    selected_matches: list[bool] = []
    forbidden_hits: set[str] = set()
    missing_slots: list[str] = []

    for expected in case.slots:
        actual = actual_slots.get(expected.subquery_id)
        relevant = {_asset_key(item) for item in expected.relevant_assets}
        forbidden = {_asset_key(item) for item in expected.forbidden_assets}
        if actual is None:
            missing_slots.append(expected.subquery_id)
            slot_status_matches.append(False)
            selected_matches.append(False)
            if relevant:
                precision_values.append(0.0)
                recall_values.append(0.0)
            continue

        slot_status_matches.append(actual.status == expected.expected_status)
        candidates = [_asset_key(item) for item in actual.candidate_assets]
        selected = {_asset_key(item) for item in actual.selected_assets}
        expected_selected = {_asset_key(item) for item in expected.expected_selected_assets}
        selected_matches.append(selected == expected_selected)

        if relevant:
            precision_values.append(float(bool(candidates and candidates[0] in relevant)))
            recall_values.append(len(relevant & set(candidates[:top_k])) / len(relevant))
        for key in forbidden & (set(candidates) | selected):
            forbidden_hits.add(_asset_key_text(key))

    actual_allowed = {_asset_key(item) for item in bundle.decision.allowed_asset_ids}
    expected_allowed = {_asset_key(item) for item in case.expected_allowed_assets}
    all_forbidden = {
        _asset_key(asset)
        for slot in case.slots
        for asset in slot.forbidden_assets
    }
    for key in actual_allowed & all_forbidden:
        forbidden_hits.add(_asset_key_text(key))

    return CaseEvaluation(
        case_id=case.case_id,
        result_present=True,
        status_match=bundle.decision.status == case.expected_status,
        slot_status_accuracy=_mean_bool(slot_status_matches) if slot_status_matches else None,
        precision_at_1=fmean(precision_values) if precision_values else None,
        recall_at_k=fmean(recall_values) if recall_values else None,
        selected_assets_match=all(selected_matches) if selected_matches else True,
        allowed_assets_match=actual_allowed == expected_allowed,
        wrong_auto_resolved=(
            bundle.decision.status == RetrievalDecisionStatus.RESOLVED
            and case.expected_status != RetrievalDecisionStatus.RESOLVED
        ),
        forbidden_asset_hits=sorted(forbidden_hits),
        missing_slots=missing_slots,
    )


def _asset_key(asset: AssetReference | ExecutableAssetReference) -> tuple[str, int, int | None]:
    return (str(asset.asset_type.value), asset.asset_id, asset.model_id)


def _asset_key_text(key: tuple[str, int, int | None]) -> str:
    asset_type, asset_id, model_id = key
    return f"{asset_type}:{asset_id}:model={model_id}"


def _mean_bool(values: list[bool]) -> float:
    return fmean(float(value) for value in values) if values else 0.0


def _mean_optional(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return fmean(present) if present else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * fraction))
    return float(ordered[rank - 1])
