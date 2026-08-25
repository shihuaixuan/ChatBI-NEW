"""统一分析 Evidence 的构建和登记入口。"""

from apps.chatbi.services.evidence.builder import (
    build_analysis_evidence,
    build_analysis_version_snapshot,
    build_research_analysis_evidence,
)
from apps.chatbi.services.evidence.registry import (
    ANALYSIS_EVIDENCE_REGISTRY_KEY,
    EvidenceRegistry,
)

__all__ = [
    "ANALYSIS_EVIDENCE_REGISTRY_KEY",
    "EvidenceRegistry",
    "build_analysis_evidence",
    "build_analysis_version_snapshot",
    "build_research_analysis_evidence",
]
