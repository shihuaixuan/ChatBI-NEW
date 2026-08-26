"""从已落库的 ResultSet 构建统一 Evidence。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from apps.chatbi.models.dto.analysis_evidence import (
    AnalysisEvidence,
    AnalysisEvidenceColumn,
    AnalysisEvidenceDependency,
    AnalysisEvidenceLevel,
    AnalysisEvidenceStatistics,
    AnalysisEvidenceVersion,
)
from apps.chatbi.models.dto.research_agent import ResearchEvidence


def build_analysis_version_snapshot(
    *,
    asset_snapshot: Mapping[str, Any] | None,
    semantic_scope: Mapping[str, Any] | None,
) -> AnalysisEvidenceVersion:
    """从冻结执行快照构建 Evidence 所需的版本指纹。"""

    snapshot = dict(asset_snapshot or {})
    scope = dict(semantic_scope or {})
    schema_version = _required_positive_int(
        snapshot.get("schema_version"),
        "ANALYSIS_EVIDENCE_SCHEMA_VERSION_REQUIRED",
    )
    contract_version = _required_positive_int(
        snapshot.get("contract_version"),
        "ANALYSIS_EVIDENCE_CONTRACT_VERSION_REQUIRED",
    )
    schema_fingerprint = _required_snapshot_string(
        snapshot.get("schema_fingerprint"),
        "ANALYSIS_EVIDENCE_SCHEMA_FINGERPRINT_REQUIRED",
    )
    scope_fingerprint = _required_snapshot_string(
        scope.get("scope_fingerprint")
        or snapshot.get("scope_fingerprint"),
        "ANALYSIS_EVIDENCE_SCOPE_FINGERPRINT_REQUIRED",
    )
    permission_fingerprint = _required_snapshot_string(
        scope.get("permission_fingerprint")
        or snapshot.get("permission_fingerprint"),
        "ANALYSIS_EVIDENCE_PERMISSION_FINGERPRINT_REQUIRED",
    )
    return AnalysisEvidenceVersion(
        schema_version=schema_version,
        contract_version=contract_version,
        schema_fingerprint=schema_fingerprint,
        scope_fingerprint=scope_fingerprint,
        permission_fingerprint=permission_fingerprint,
    )


def build_analysis_evidence(
    *,
    run_id: str,
    mode: Literal["agent", "fast", "plan", "research"],
    plan_id: str,
    node_id: str,
    tool_call_id: str,
    result_set_id: str,
    fields: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
    row_count: int,
    metric_refs: Sequence[str] = (),
    dimension_refs: Sequence[str] = (),
    time_roles: Sequence[str] = (),
    filters: Sequence[Mapping[str, Any]] = (),
    purpose: str | None = None,
    iteration: int = 0,
    dependencies: Sequence[AnalysisEvidenceDependency] = (),
    version_snapshot: AnalysisEvidenceVersion,
    logical_columns: Sequence[AnalysisEvidenceColumn] | None = None,
    evidence_level: AnalysisEvidenceLevel = AnalysisEvidenceLevel.GOVERNED,
    limitations: Sequence[str] = (),
) -> AnalysisEvidence:
    """构建查询或计算节点 Evidence，完整结果继续只存放在 ResultStore。"""

    normalized_rows = tuple(dict(row) for row in rows)
    normalized_fields = tuple(str(field) for field in fields)
    columns = tuple(
        logical_columns
        or _logical_columns(
            metric_refs=metric_refs,
            dimension_refs=dimension_refs,
            fields=normalized_fields,
        )
    )
    evidence_run_id = run_id
    normalized_filters = tuple(dict(item) for item in filters)
    return AnalysisEvidence(
        run_id=evidence_run_id,
        evidence_id=f"evidence:{mode}:{plan_id}:{node_id}",
        mode=mode,
        plan_id=plan_id,
        node_id=node_id,
        source_tool_call_id=f"{mode}:{tool_call_id}",
        result_set_id=result_set_id,
        iteration=iteration,
        purpose=purpose or f"{mode} 执行节点 {node_id}",
        metric_refs=tuple(metric_refs),
        dimension_refs=tuple(dimension_refs),
        time_roles=tuple(time_roles),
        filters=normalized_filters,
        logical_columns=columns,
        statistics=AnalysisEvidenceStatistics(
            row_count=max(row_count, 0),
            truncated=row_count > len(normalized_rows),
        ),
        sample_rows=normalized_rows[:10],
        dependencies=tuple(dependencies),
        evidence_level=evidence_level,
        version_snapshot=version_snapshot,
        limitations=tuple(limitations),
    )


def build_research_analysis_evidence(
    evidence: ResearchEvidence,
    *,
    run_id: str,
) -> AnalysisEvidence:
    """把现有 Research Evidence 投影到统一台账。"""

    return AnalysisEvidence(
        run_id=run_id,
        evidence_id=evidence.evidence_id,
        mode="research",
        plan_id="research",
        node_id=evidence.source_tool_call.tool_call_id,
        source_tool_call_id=evidence.source_tool_call.tool_call_id,
        result_set_id=evidence.result_ref.result_id,
        iteration=evidence.iteration,
        purpose=evidence.purpose,
        metric_refs=evidence.metric_refs,
        dimension_refs=evidence.dimension_refs,
        time_roles=tuple(item.value for item in evidence.time_ranges),
        filters=tuple(item.model_dump(mode="json") for item in evidence.filters),
        logical_columns=tuple(
            AnalysisEvidenceColumn(
                asset_ref=item.asset_ref,
                value_role=item.value_role,
                result_field=item.result_field,
            )
            for item in evidence.logical_columns
        ),
        statistics=AnalysisEvidenceStatistics(
            row_count=evidence.statistics.row_count,
            truncated=evidence.statistics.truncated,
        ),
        sample_rows=evidence.sample_rows,
        dependencies=tuple(
            AnalysisEvidenceDependency(
                evidence_id=item.evidence_id,
                relation=item.relation,
                source_iteration=item.source_iteration,
            )
            for item in evidence.dependencies
        ),
        evidence_level=AnalysisEvidenceLevel(evidence.evidence_level.value),
        version_snapshot=AnalysisEvidenceVersion(
            schema_version=evidence.version_snapshot.schema_version,
            contract_version=evidence.version_snapshot.contract_version,
            schema_fingerprint=evidence.version_snapshot.schema_fingerprint,
            scope_fingerprint=evidence.version_snapshot.scope_fingerprint,
            permission_fingerprint=evidence.version_snapshot.permission_fingerprint,
        ),
        limitations=evidence.limitations,
    )


def _logical_columns(
    *,
    metric_refs: Sequence[str],
    dimension_refs: Sequence[str],
    fields: Sequence[str],
) -> tuple[AnalysisEvidenceColumn, ...]:
    """在阶段 4 精确列映射前，按冻结查询声明生成有界的逻辑列投影。"""

    result: list[AnalysisEvidenceColumn] = []
    used_fields: set[str] = set()
    for index, ref in enumerate(dimension_refs):
        field = _field_at(fields, index)
        if field is not None:
            used_fields.add(field)
        result.append(
            AnalysisEvidenceColumn(
                asset_ref=ref,
                value_role="group_key",
                result_field=field,
            )
        )
    for index, ref in enumerate(metric_refs):
        field = _next_unused_field(fields, used_fields, index)
        if field is not None:
            used_fields.add(field)
        result.append(
            AnalysisEvidenceColumn(
                asset_ref=ref,
                value_role="value",
                result_field=field,
            )
        )
    if not result:
        for field in fields:
            result.append(
                AnalysisEvidenceColumn(
                    asset_ref="ASSET:result",
                    value_role="value",
                    result_field=str(field),
                )
            )
    return tuple(result)


def _field_at(fields: Sequence[str], index: int) -> str | None:
    return fields[index] if index < len(fields) else None


def _next_unused_field(
    fields: Sequence[str], used_fields: set[str], metric_index: int
) -> str | None:
    available = [field for field in fields if field not in used_fields]
    return available[metric_index] if metric_index < len(available) else None


def _required_positive_int(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(code)
    return value


def _required_snapshot_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value.strip()


__all__ = [
    "build_analysis_evidence",
    "build_analysis_version_snapshot",
    "build_research_analysis_evidence",
]
