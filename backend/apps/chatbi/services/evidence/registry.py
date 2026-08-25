"""统一分析 Evidence 台账。

Fast、Plan 和 Research 共用本台账。Research 的历史研究状态仍保留自己的
投影，便于恢复旧快照；统一台账是跨模式执行结果的共同事实入口。
"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidence

ANALYSIS_EVIDENCE_REGISTRY_KEY = "analysis_evidence"


class EvidenceRegistry:
    """把 Evidence 按当前 Run 登记到可持久化的运行状态。"""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def get(self, evidence_id: str) -> AnalysisEvidence | None:
        raw = self._mapping().get(evidence_id)
        if not isinstance(raw, dict):
            return None
        return AnalysisEvidence.model_validate(raw)

    def evidences(self) -> tuple[AnalysisEvidence, ...]:
        return tuple(
            AnalysisEvidence.model_validate(raw)
            for raw in self._mapping().values()
            if isinstance(raw, dict)
        )

    def register(self, evidence: AnalysisEvidence) -> None:
        """登记 Evidence，并在写入前校验整棵 Evidence DAG。"""

        existing = self.get(evidence.evidence_id)
        if existing is not None:
            if existing == evidence:
                return
        candidate = [
            item for item in self.evidences() if item.evidence_id != evidence.evidence_id
        ]
        candidate.append(evidence)
        _validate_registry(candidate, evidence.run_id)
        self._mapping()[evidence.evidence_id] = evidence.model_dump(mode="json")

    def merge(self, evidences: tuple[AnalysisEvidence, ...]) -> None:
        """恢复或迁移时批量登记 Evidence，任何一条失败都不写入部分结果。"""

        candidate = list(self.evidences())
        by_id = {item.evidence_id: item for item in candidate}
        for evidence in evidences:
            # 同一 Evidence ID 允许补齐服务端生成的列映射，但最终仍需整图校验。
            by_id[evidence.evidence_id] = evidence
        merged = list(by_id.values())
        if merged:
            run_id = evidences[0].run_id if evidences else merged[0].run_id
            _validate_registry(merged, run_id)
        self._state[ANALYSIS_EVIDENCE_REGISTRY_KEY] = {
            item.evidence_id: item.model_dump(mode="json") for item in merged
        }

    def merge_missing(self, evidences: tuple[AnalysisEvidence, ...]) -> None:
        """兼容旧状态时只补建缺失 Evidence，不覆盖统一台账中的规范记录。"""

        by_id = {item.evidence_id: item for item in self.evidences()}
        for evidence in evidences:
            by_id.setdefault(evidence.evidence_id, evidence)
        merged = list(by_id.values())
        if merged:
            _validate_registry(merged, merged[0].run_id)
        self._state[ANALYSIS_EVIDENCE_REGISTRY_KEY] = {
            item.evidence_id: item.model_dump(mode="json") for item in merged
        }

    def _mapping(self) -> dict[str, Any]:
        value = self._state.setdefault(ANALYSIS_EVIDENCE_REGISTRY_KEY, {})
        if not isinstance(value, dict):
            raise TypeError("ANALYSIS_EVIDENCE_REGISTRY_INVALID")
        return value


def _validate_registry(
    evidences: list[AnalysisEvidence],
    run_id: str,
) -> None:
    """校验统一台账的 Run 和依赖边界。"""

    if any(item.run_id != run_id for item in evidences):
        raise ValueError("ANALYSIS_EVIDENCE_CROSS_RUN")
    known = {item.evidence_id for item in evidences}
    for item in evidences:
        if any(
            dependency.evidence_id not in known
            for dependency in item.dependencies
        ):
            raise ValueError("ANALYSIS_EVIDENCE_DEPENDENCY_NOT_FOUND")


__all__ = ["ANALYSIS_EVIDENCE_REGISTRY_KEY", "EvidenceRegistry"]
