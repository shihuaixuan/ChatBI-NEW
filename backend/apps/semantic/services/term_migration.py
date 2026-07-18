"""旧术语迁入 Semantic 的确定性规划规则。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.semantic.models.dto import TermPayload
from apps.semantic.utils.text import unique_texts

TermKey = tuple[int, int, str]
AssetDomainKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class LegacyTermSnapshot:
    """旧术语及其子词的稳定输入快照。"""

    source_id: int
    oid: int | None
    word: str | None
    description: str | None = None
    other_words: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    dataset_ids: tuple[int, ...] = ()
    mapped_assets: tuple[Mapping[str, Any], ...] = ()
    specific_ds: bool = False
    datasource_ids: tuple[int, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ExistingSemanticTermSnapshot:
    """用于判定迁移幂等性的目标术语快照。"""

    target_id: int
    oid: int
    domain_id: int
    name: str
    alias: tuple[str, ...] = ()
    description: str | None = None
    related_datasets: tuple[int, ...] = ()
    related_metrics: tuple[int, ...] = ()
    related_dimensions: tuple[int, ...] = ()
    status: int = 1


@dataclass(frozen=True, slots=True)
class LegacyTermMigrationContext:
    """迁移所需的有效引用和目标数据快照。"""

    active_domains: frozenset[AssetDomainKey]
    dataset_domains: Mapping[AssetDomainKey, int]
    metric_domains: Mapping[AssetDomainKey, int]
    dimension_domains: Mapping[AssetDomainKey, int]
    default_domains: Mapping[int, int] = field(default_factory=dict)
    existing_terms: Mapping[TermKey, ExistingSemanticTermSnapshot] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class SemanticTermMigrationCandidate:
    """已经通过引用和冲突校验、可以写入的术语。"""

    source_id: int
    oid: int
    domain_id: int
    name: str
    alias: tuple[str, ...]
    description: str | None
    related_datasets: tuple[int, ...]
    related_metrics: tuple[int, ...]
    related_dimensions: tuple[int, ...]
    status: int

    def to_payload(self) -> TermPayload:
        return TermPayload(
            domain_id=self.domain_id,
            name=self.name,
            alias=list(self.alias),
            description=self.description,
            related_datasets=list(self.related_datasets),
            related_metrics=list(self.related_metrics),
            related_dimensions=list(self.related_dimensions),
        )


@dataclass(frozen=True, slots=True)
class TermMigrationIssue:
    source_id: int
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class TermMigrationSkip:
    source_id: int
    target_id: int


@dataclass(frozen=True, slots=True)
class LegacyTermMigrationPlan:
    """迁移预检结果；存在冲突或失败时不得直接写入。"""

    total_count: int
    candidates: tuple[SemanticTermMigrationCandidate, ...]
    skipped: tuple[TermMigrationSkip, ...]
    conflicts: tuple[TermMigrationIssue, ...]
    failures: tuple[TermMigrationIssue, ...]

    @property
    def success_count(self) -> int:
        return len(self.candidates)

    @property
    def can_apply(self) -> bool:
        return not self.conflicts and not self.failures

    def to_summary(self) -> dict[str, Any]:
        return {
            "total_count": self.total_count,
            "success_count": self.success_count,
            "skipped_count": len(self.skipped),
            "conflict_count": len(self.conflicts),
            "failure_count": len(self.failures),
            "can_apply": self.can_apply,
            "conflicts": [_issue_dict(issue) for issue in self.conflicts],
            "failures": [_issue_dict(issue) for issue in self.failures],
        }


class LegacyTermMigrationPlanner:
    """把旧术语转换为可审查、可重复执行的 Semantic 写入计划。"""

    def plan(
        self,
        records: Sequence[LegacyTermSnapshot],
        context: LegacyTermMigrationContext,
    ) -> LegacyTermMigrationPlan:
        candidates: list[SemanticTermMigrationCandidate] = []
        skipped: list[TermMigrationSkip] = []
        conflicts: list[TermMigrationIssue] = []
        failures: list[TermMigrationIssue] = []
        planned_keys: set[TermKey] = set()

        for record in records:
            candidate, issue, issue_kind = self._candidate(record, context)
            if issue is not None:
                (conflicts if issue_kind == "conflict" else failures).append(issue)
                continue
            if candidate is None:
                raise AssertionError("术语迁移候选和问题不能同时为空")

            key = _term_key(candidate.oid, candidate.domain_id, candidate.name)
            existing = context.existing_terms.get(key)
            if existing is not None:
                if _matches_existing(candidate, existing):
                    skipped.append(
                        TermMigrationSkip(
                            source_id=record.source_id,
                            target_id=existing.target_id,
                        )
                    )
                else:
                    conflicts.append(
                        TermMigrationIssue(
                            source_id=record.source_id,
                            code="SEMANTIC_TERM_NAME_CONFLICT",
                            detail="目标主题域已存在同名但内容不同的术语",
                        )
                    )
                continue
            if key in planned_keys:
                conflicts.append(
                    TermMigrationIssue(
                        source_id=record.source_id,
                        code="LEGACY_TERM_DUPLICATE_IN_BATCH",
                        detail="同一批次包含相同租户、主题域和名称的多个旧术语",
                    )
                )
                continue

            planned_keys.add(key)
            candidates.append(candidate)

        return LegacyTermMigrationPlan(
            total_count=len(records),
            candidates=tuple(candidates),
            skipped=tuple(skipped),
            conflicts=tuple(conflicts),
            failures=tuple(failures),
        )

    def _candidate(
        self,
        record: LegacyTermSnapshot,
        context: LegacyTermMigrationContext,
    ) -> tuple[
        SemanticTermMigrationCandidate | None,
        TermMigrationIssue | None,
        str | None,
    ]:
        if record.source_id <= 0:
            return _failure(record, "LEGACY_TERM_ID_INVALID", "旧术语 ID 必须为正整数")
        if record.oid is None or record.oid <= 0:
            return _failure(record, "LEGACY_TERM_TENANT_INVALID", "旧术语缺少有效租户")

        name = str(record.word or "").strip()
        if not name:
            return _failure(record, "LEGACY_TERM_NAME_EMPTY", "旧术语名称不能为空")
        if len(name) > 128:
            return _failure(
                record,
                "LEGACY_TERM_NAME_TOO_LONG",
                "旧术语名称超过 SemanticTerm 的 128 字符限制",
            )
        if record.specific_ds:
            return _failure(
                record,
                "LEGACY_TERM_DATASOURCE_SCOPE_UNRESOLVED",
                "旧术语使用数据源范围，必须先显式转换为数据集范围",
            )
        if any(_positive_id(value) is None for value in record.dataset_ids):
            return _failure(
                record,
                "LEGACY_TERM_DATASET_ID_INVALID",
                "旧术语的数据集范围包含无效 ID",
            )

        dataset_ids = tuple(_unique_positive_ids(record.dataset_ids))
        domain_id, issue, issue_kind = _resolve_domain(record, dataset_ids, context)
        if issue is not None:
            return None, issue, issue_kind
        if domain_id is None:
            raise AssertionError("术语迁移主题域解析结果不能为空")

        related_metrics, related_dimensions, asset_issue = _mapped_assets(
            record,
            domain_id,
            context,
        )
        if asset_issue is not None:
            return None, asset_issue, "failure"

        aliases = unique_texts([*record.other_words, *record.aliases])
        aliases = [alias for alias in aliases if alias != name]
        return (
            SemanticTermMigrationCandidate(
                source_id=record.source_id,
                oid=record.oid,
                domain_id=domain_id,
                name=name,
                alias=tuple(aliases),
                description=_optional_text(record.description),
                related_datasets=dataset_ids,
                related_metrics=tuple(related_metrics),
                related_dimensions=tuple(related_dimensions),
                status=1 if record.enabled else 0,
            ),
            None,
            None,
        )


def _resolve_domain(
    record: LegacyTermSnapshot,
    dataset_ids: tuple[int, ...],
    context: LegacyTermMigrationContext,
) -> tuple[int | None, TermMigrationIssue | None, str | None]:
    oid = record.oid
    if oid is None:
        raise AssertionError("解析主题域前必须先校验租户")

    domain_id: int | None
    if dataset_ids:
        missing = [
            dataset_id
            for dataset_id in dataset_ids
            if (oid, dataset_id) not in context.dataset_domains
        ]
        if missing:
            return _failure(
                record,
                "LEGACY_TERM_DATASET_NOT_FOUND",
                f"以下数据集不存在或已停用: {missing}",
            )
        domain_ids = {context.dataset_domains[(oid, value)] for value in dataset_ids}
        if len(domain_ids) != 1:
            return _conflict(
                record,
                "LEGACY_TERM_MULTIPLE_DOMAINS",
                f"数据集分属多个主题域: {sorted(domain_ids)}",
            )
        domain_id = next(iter(domain_ids))
    else:
        domain_id = context.default_domains.get(oid)
        if domain_id is None:
            return _failure(
                record,
                "LEGACY_TERM_DOMAIN_UNRESOLVED",
                "旧术语没有数据集范围，且未配置租户默认主题域",
            )

    if (oid, domain_id) not in context.active_domains:
        return _failure(
            record,
            "SEMANTIC_DOMAIN_NOT_FOUND",
            f"目标主题域不存在或已停用: {domain_id}",
        )
    return domain_id, None, None


def _mapped_assets(
    record: LegacyTermSnapshot,
    domain_id: int,
    context: LegacyTermMigrationContext,
) -> tuple[list[int], list[int], TermMigrationIssue | None]:
    oid = record.oid
    if oid is None:
        raise AssertionError("转换关联资产前必须先校验租户")

    metric_ids: list[int] = []
    dimension_ids: list[int] = []
    for asset in record.mapped_assets:
        asset_type = str(
            asset.get("asset_type") or asset.get("assetType") or asset.get("type") or ""
        ).upper()
        asset_id = _positive_id(
            asset.get("asset_id") or asset.get("assetId") or asset.get("id")
        )
        if asset_type not in {"METRIC", "DIMENSION"} or asset_id is None:
            return [], [], TermMigrationIssue(
                source_id=record.source_id,
                code="LEGACY_TERM_ASSET_INVALID",
                detail=f"无法识别关联资产: {dict(asset)}",
            )

        domain_map = (
            context.metric_domains
            if asset_type == "METRIC"
            else context.dimension_domains
        )
        asset_domain = domain_map.get((oid, asset_id))
        if asset_domain is None:
            return [], [], TermMigrationIssue(
                source_id=record.source_id,
                code="LEGACY_TERM_ASSET_NOT_FOUND",
                detail=f"关联的 {asset_type} 资产不存在或已停用: {asset_id}",
            )
        if asset_domain != domain_id:
            return [], [], TermMigrationIssue(
                source_id=record.source_id,
                code="LEGACY_TERM_ASSET_DOMAIN_MISMATCH",
                detail=f"关联的 {asset_type} 资产不属于目标主题域: {asset_id}",
            )
        target = metric_ids if asset_type == "METRIC" else dimension_ids
        if asset_id not in target:
            target.append(asset_id)
    return metric_ids, dimension_ids, None


def _matches_existing(
    candidate: SemanticTermMigrationCandidate,
    existing: ExistingSemanticTermSnapshot,
) -> bool:
    return (
        candidate.oid == existing.oid
        and candidate.domain_id == existing.domain_id
        and candidate.name == existing.name.strip()
        and candidate.alias == tuple(unique_texts(existing.alias))
        and candidate.description == _optional_text(existing.description)
        and candidate.related_datasets == tuple(_unique_positive_ids(existing.related_datasets))
        and candidate.related_metrics == tuple(_unique_positive_ids(existing.related_metrics))
        and candidate.related_dimensions
        == tuple(_unique_positive_ids(existing.related_dimensions))
        and candidate.status == existing.status
    )


def _term_key(oid: int, domain_id: int, name: str) -> TermKey:
    return oid, domain_id, name.strip()


def _unique_positive_ids(values: Sequence[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        item = _positive_id(value)
        if item is not None and item not in result:
            result.append(item)
    return result


def _positive_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _failure(
    record: LegacyTermSnapshot,
    code: str,
    detail: str,
) -> tuple[None, TermMigrationIssue, str]:
    return None, TermMigrationIssue(record.source_id, code, detail), "failure"


def _conflict(
    record: LegacyTermSnapshot,
    code: str,
    detail: str,
) -> tuple[None, TermMigrationIssue, str]:
    return None, TermMigrationIssue(record.source_id, code, detail), "conflict"


def _issue_dict(issue: TermMigrationIssue) -> dict[str, int | str]:
    return {
        "source_id": issue.source_id,
        "code": issue.code,
        "detail": issue.detail,
    }


__all__ = [
    "ExistingSemanticTermSnapshot",
    "LegacyTermMigrationContext",
    "LegacyTermMigrationPlan",
    "LegacyTermMigrationPlanner",
    "LegacyTermSnapshot",
    "SemanticTermMigrationCandidate",
    "TermMigrationIssue",
    "TermMigrationSkip",
]
