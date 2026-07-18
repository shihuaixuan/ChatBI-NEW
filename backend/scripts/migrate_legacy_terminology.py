"""预检并迁移旧 terminology 表到 SemanticTerm。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR  # type: ignore[import-untyped]
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, select

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticTerm,
)
from apps.semantic.repository.sqlmodel.storage_sync import sync_term_relations
from apps.semantic.services.term_migration import (
    ExistingSemanticTermSnapshot,
    LegacyTermMigrationContext,
    LegacyTermMigrationPlan,
    LegacyTermMigrationPlanner,
    LegacyTermSnapshot,
)
from common.core.db import engine


class LegacyTerminology(SQLModel, table=True):
    """仅供下线脚本读取旧表的临时映射，不属于运行时领域模型。"""

    __tablename__ = "terminology"

    id: int | None = Field(
        sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )
    oid: int | None = Field(sa_column=Column(BigInteger, nullable=True, default=1))
    pid: int | None = Field(sa_column=Column(BigInteger, nullable=True))
    create_time: datetime | None = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    word: str | None = Field(max_length=255)
    description: str | None = Field(sa_column=Column(Text, nullable=True))
    embedding: list[float] | None = Field(sa_column=Column(VECTOR(), nullable=True))
    aliases: list[str] | None = Field(sa_column=Column(JSONB, nullable=True))
    dataset_ids: list[int] | None = Field(sa_column=Column(JSONB, nullable=True))
    mapped_assets: list[dict[str, Any]] | None = Field(
        sa_column=Column(JSONB, nullable=True)
    )
    specific_ds: bool | None = Field(sa_column=Column(Boolean, nullable=True))
    datasource_ids: list[int] | None = Field(sa_column=Column(JSONB, nullable=True))
    enabled: bool | None = Field(sa_column=Column(Boolean, nullable=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="预检并迁移旧 terminology 表；默认只输出预检报告",
    )
    parser.add_argument("--oid", type=int, help="只处理指定租户")
    parser.add_argument(
        "--default-domain",
        action="append",
        default=[],
        metavar="OID:DOMAIN_ID",
        help="为没有数据集范围的旧术语指定租户默认主题域，可重复传入",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="预检无冲突和失败时写入；未指定时只生成报告",
    )
    parser.add_argument(
        "--purge-source",
        action="store_true",
        help="迁移成功后删除本次范围内的旧表记录；必须与 --apply 同时使用",
    )
    return parser.parse_args()


def parse_default_domains(values: Sequence[str]) -> dict[int, int]:
    """解析并校验 OID:DOMAIN_ID 参数。"""

    result: dict[int, int] = {}
    for value in values:
        parts = value.split(":", maxsplit=1)
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            raise ValueError(f"默认主题域参数格式错误: {value}")
        oid, domain_id = (int(part.strip()) for part in parts)
        if oid <= 0 or domain_id <= 0:
            raise ValueError(f"默认主题域参数必须为正整数: {value}")
        previous = result.get(oid)
        if previous is not None and previous != domain_id:
            raise ValueError(f"同一租户配置了多个默认主题域: {oid}")
        result[oid] = domain_id
    return result


def build_legacy_snapshots(
    rows: Sequence[LegacyTerminology],
) -> list[LegacyTermSnapshot]:
    """把父子行结构合并为迁移规划器需要的术语快照。"""

    children_by_parent: dict[int, list[str]] = {}
    for row in rows:
        if row.pid is None or not row.word:
            continue
        children_by_parent.setdefault(row.pid, []).append(row.word)

    snapshots: list[LegacyTermSnapshot] = []
    for row in sorted(
        (item for item in rows if item.pid is None),
        key=lambda item: item.id or 0,
    ):
        snapshots.append(
            LegacyTermSnapshot(
                source_id=row.id or 0,
                oid=row.oid,
                word=row.word,
                description=row.description,
                other_words=tuple(children_by_parent.get(row.id or 0, [])),
                aliases=_text_tuple(row.aliases),
                dataset_ids=_id_tuple(row.dataset_ids),
                mapped_assets=tuple(row.mapped_assets or []),
                specific_ds=bool(row.specific_ds),
                datasource_ids=_id_tuple(row.datasource_ids),
                enabled=row.enabled is not False,
            )
        )
    return snapshots


def build_migration_context(
    *,
    domains: Sequence[SemanticDomain],
    datasets: Sequence[SemanticDataset],
    dataset_model_configs: Sequence[SemanticDatasetModelConfig],
    models: Sequence[SemanticModel],
    metrics: Sequence[SemanticMetric],
    dimensions: Sequence[SemanticDimension],
    terms: Sequence[SemanticTerm],
    default_domains: dict[int, int],
) -> LegacyTermMigrationContext:
    """从目标表快照建立租户隔离的有效引用映射。"""

    active_domains = frozenset(
        (domain.oid, domain.id)
        for domain in domains
        if domain.id is not None and domain.status == 1
    )
    dataset_domains = {
        (dataset.oid, dataset.id): dataset.domain_id
        for dataset in datasets
        if dataset.id is not None and dataset.status == 1
    }
    model_domains = {
        (model.oid, model.id): model.domain_id
        for model in models
        if model.id is not None and model.status == 1
    }
    active_models = {
        (model.oid, model.id): model
        for model in models
        if model.id is not None and model.status == 1
    }
    datasource_dataset_lists: dict[tuple[int, int], list[int]] = {}
    for config in dataset_model_configs:
        if config.status != 1 or (config.oid, config.dataset_id) not in dataset_domains:
            continue
        model = active_models.get((config.oid, config.model_id))
        if model is None:
            continue
        datasource_key = (config.oid, model.datasource_id)
        dataset_ids = datasource_dataset_lists.setdefault(datasource_key, [])
        if config.dataset_id not in dataset_ids:
            dataset_ids.append(config.dataset_id)
    datasource_datasets = {
        datasource_key: tuple(dataset_ids)
        for datasource_key, dataset_ids in datasource_dataset_lists.items()
    }
    metric_domains = {
        (metric.oid, metric.id): model_domains[(metric.oid, metric.model_id)]
        for metric in metrics
        if metric.id is not None
        and metric.status == 1
        and (metric.oid, metric.model_id) in model_domains
    }
    dimension_domains = {
        (dimension.oid, dimension.id): model_domains[
            (dimension.oid, dimension.model_id)
        ]
        for dimension in dimensions
        if dimension.id is not None
        and dimension.status == 1
        and (dimension.oid, dimension.model_id) in model_domains
    }

    existing_terms: dict[tuple[int, int, str], ExistingSemanticTermSnapshot] = {}
    for term in terms:
        if term.id is None:
            continue
        term_key = (term.oid, term.domain_id, term.name.strip())
        if term_key in existing_terms:
            raise ValueError(
                f"目标表存在重复术语，无法判定幂等迁移: oid={term.oid}, "
                f"domain_id={term.domain_id}, name={term.name}"
            )
        existing_terms[term_key] = ExistingSemanticTermSnapshot(
            target_id=term.id,
            oid=term.oid,
            domain_id=term.domain_id,
            name=term.name,
            alias=tuple(term.alias or []),
            description=term.description,
            related_datasets=tuple(term.related_datasets or []),
            related_metrics=tuple(term.related_metrics or []),
            related_dimensions=tuple(term.related_dimensions or []),
            status=term.status,
        )

    return LegacyTermMigrationContext(
        active_domains=active_domains,
        dataset_domains=dataset_domains,
        datasource_datasets=datasource_datasets,
        metric_domains=metric_domains,
        dimension_domains=dimension_domains,
        default_domains=default_domains,
        existing_terms=existing_terms,
    )


def apply_plan(session: Session, plan: LegacyTermMigrationPlan) -> int:
    """写入全部候选；事务提交由迁移入口统一控制。"""

    if not plan.can_apply:
        raise ValueError("术语迁移计划包含冲突或失败，禁止写入")

    created: list[SemanticTerm] = []
    for candidate in plan.candidates:
        term = SemanticTerm(
            oid=candidate.oid,
            domain_id=candidate.domain_id,
            name=candidate.name,
            alias=list(candidate.alias),
            description=candidate.description,
            related_datasets=list(candidate.related_datasets),
            related_metrics=list(candidate.related_metrics),
            related_dimensions=list(candidate.related_dimensions),
            status=candidate.status,
        )
        session.add(term)
        created.append(term)

    session.flush()
    for term in created:
        sync_term_relations(session, term)
    return len(created)


def validate_source_rows_for_purge(rows: Sequence[LegacyTerminology]) -> None:
    """确认每条旧记录都能被迁移流程解释，禁止静默删除异常子记录。"""

    parent_ids = {row.id for row in rows if row.id is not None and row.pid is None}
    for row in rows:
        if row.id is None:
            raise ValueError("旧术语存在无主键记录，禁止清理源数据")
        if row.pid is None:
            continue
        if row.pid not in parent_ids:
            raise ValueError(
                f"旧术语子记录缺少父记录，禁止清理源数据: id={row.id}, pid={row.pid}"
            )
        if not row.word or not row.word.strip():
            raise ValueError(f"旧术语子记录名称为空，禁止清理源数据: id={row.id}")


def run(
    session: Session,
    *,
    oid: int | None,
    default_domains: dict[int, int],
    apply: bool,
    purge_source: bool = False,
) -> tuple[dict[str, Any], int]:
    """执行预检，并在明确要求且结果可写时应用。"""

    if purge_source and not apply:
        raise ValueError("--purge-source 必须与 --apply 同时使用")

    legacy_rows = _all(session, LegacyTerminology, oid)
    domains = _all(session, SemanticDomain, oid)
    datasets = _all(session, SemanticDataset, oid)
    dataset_model_configs = _all(session, SemanticDatasetModelConfig, oid)
    models = _all(session, SemanticModel, oid)
    metrics = _all(session, SemanticMetric, oid)
    dimensions = _all(session, SemanticDimension, oid)
    terms = _all(session, SemanticTerm, oid)

    plan = LegacyTermMigrationPlanner().plan(
        build_legacy_snapshots(legacy_rows),
        build_migration_context(
            domains=domains,
            datasets=datasets,
            dataset_model_configs=dataset_model_configs,
            models=models,
            metrics=metrics,
            dimensions=dimensions,
            terms=terms,
            default_domains=default_domains,
        ),
    )
    report = {
        **plan.to_summary(),
        "mode": "apply-and-purge" if purge_source else "apply" if apply else "dry-run",
        "candidate_source_ids": [item.source_id for item in plan.candidates],
        "skipped": [
            {"source_id": item.source_id, "target_id": item.target_id}
            for item in plan.skipped
        ],
    }
    if apply:
        if not plan.can_apply:
            report["applied_count"] = 0
            report["purged_count"] = 0
            return report, 2
        report["applied_count"] = apply_plan(session, plan)
        report["purged_count"] = 0
        if purge_source:
            validate_source_rows_for_purge(legacy_rows)
            for row in legacy_rows:
                session.delete(row)
            report["purged_count"] = len(legacy_rows)
        session.commit()
    return report, 0


def main() -> int:
    args = parse_args()
    if args.oid is not None and args.oid <= 0:
        raise ValueError("--oid 必须为正整数")
    if args.purge_source and not args.apply:
        raise ValueError("--purge-source 必须与 --apply 同时使用")
    default_domains = parse_default_domains(args.default_domain)
    with Session(engine) as session:
        report, exit_code = run(
            session,
            oid=args.oid,
            default_domains=default_domains,
            apply=args.apply,
            purge_source=args.purge_source,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


def _all(session: Session, model: Any, oid: int | None) -> list[Any]:
    statement = select(model)
    if oid is not None:
        statement = statement.where(model.oid == oid)
    return list(session.exec(statement).all())


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _id_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(value)


if __name__ == "__main__":
    raise SystemExit(main())
