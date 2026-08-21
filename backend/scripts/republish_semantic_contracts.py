#!/usr/bin/env python3
"""为历史快照缺失或索引未激活的数据集补齐语义发布和检索索引。

默认只检查并输出待处理数据集。生产执行必须显式传入 ``--apply``；如需立即
处理检索任务，再额外传入 ``--process-index``，否则脚本只创建 durable index job。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

# 支持从仓库根目录直接执行该运维脚本。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.retrieval.indexing.worker import process_index_jobs  # noqa: E402
from apps.retrieval.models.orm import (  # noqa: E402
    RetrievalIndexGenerationModel,
    RetrievalIndexJobModel,
    RetrievalSourceModel,
)
from apps.retrieval.sources.semantic_indexing import (  # noqa: E402
    SemanticIndexCoordinator,
)
from apps.semantic.errors import SemanticError  # noqa: E402
from apps.semantic.models.dto import DatasetSchema  # noqa: E402
from apps.semantic.models.orm import SemanticDataset  # noqa: E402
from apps.semantic.models.orm.contract_version import (  # noqa: E402
    SemanticContractVersion,
)
from apps.semantic.repository.sqlmodel.dataset_index_repository import (  # noqa: E402
    SqlModelDatasetIndexRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import (  # noqa: E402
    SemanticSchemaLoader,
)
from apps.semantic.repository.sqlmodel.semantic_contract_repository import (  # noqa: E402
    SqlModelSemanticContractRepository,
)
from apps.semantic.services.contract_publication_service import (  # noqa: E402
    SemanticContractPublicationService,
)
from apps.semantic.services.dataset_index_service import (  # noqa: E402
    SemanticDatasetIndexService,
)
from apps.semantic.services.schema_service import SemanticSchemaService  # noqa: E402
from common.core.db import engine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重新发布旧语义契约并重建检索索引")
    parser.add_argument("--oid", type=int, help="只处理指定租户")
    parser.add_argument("--dataset-id", type=int, help="只处理指定数据集")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行重新发布和索引任务创建；默认只输出检查结果",
    )
    parser.add_argument(
        "--process-index",
        action="store_true",
        help="在创建索引任务后立即执行；需要完整 embedding 配置",
    )
    return parser.parse_args()


def _latest_version(session: Session, dataset: SemanticDataset) -> SemanticContractVersion | None:
    """读取数据集最新版本，避免只根据数据集当前版本判断快照是否完整。"""

    return session.exec(
        select(SemanticContractVersion)
        .where(
            SemanticContractVersion.oid == dataset.oid,
            SemanticContractVersion.dataset_id == dataset.id,
        )
        .order_by(col(SemanticContractVersion.contract_version).desc())
        .limit(1)
    ).one_or_none()


def _has_valid_schema_snapshot(version: SemanticContractVersion | None) -> bool:
    """不仅检查 schema 键，还验证快照可以还原为正式 DatasetSchema。"""

    if version is None:
        return False
    schema = version.asset_snapshot.get("schema")
    if not isinstance(schema, dict):
        return False
    try:
        DatasetSchema.model_validate(schema)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class _IndexRepairState:
    """当前 Schema 对应检索 generation 的可恢复状态。"""

    status: str
    generation: RetrievalIndexGenerationModel | None = None
    # 只允许脚本主动处理 pending；running 任务由现有 worker 继续执行。
    job_ids: tuple[int, ...] = ()
    running_job_ids: tuple[int, ...] = ()


def _index_status_from_state(status: str) -> str:
    """把持久化索引状态转换为运维报告状态。"""

    return {
        "READY": "READY",
        "IN_PROGRESS": "IN_PROGRESS",
        "FAILED": "FAILED",
        "MISSING": "MISSING",
    }.get(status, status)


def _index_repair_state(
    session: Session,
    dataset: SemanticDataset,
    version: SemanticContractVersion,
) -> _IndexRepairState:
    """区分已就绪、进行中、失败和缺失，避免重复创建 generation。"""

    if dataset.id is None or dataset.index_version <= 0:
        return _IndexRepairState("MISSING")
    expected_source_version = (
        f"schema:{version.schema_version}:index:{dataset.index_version}"
    )
    source = session.exec(
        select(RetrievalSourceModel).where(
            RetrievalSourceModel.tenant_id == dataset.oid,
            RetrievalSourceModel.source_type == "headless",
            RetrievalSourceModel.source_key == f"dataset:{dataset.id}",
        )
    ).one_or_none()
    if source is None or source.id is None:
        return _IndexRepairState("MISSING")
    generations = list(session.exec(
        select(RetrievalIndexGenerationModel).where(
            RetrievalIndexGenerationModel.tenant_id == dataset.oid,
            RetrievalIndexGenerationModel.source_id == source.id,
            RetrievalIndexGenerationModel.source_version == expected_source_version,
            col(RetrievalIndexGenerationModel.status).in_([
                "active",
                "building",
                "ready",
                "failed",
            ]),
        )
        .order_by(col(RetrievalIndexGenerationModel.id).desc())
    ).all())
    generation = generations[0] if generations else None
    if generation is None or generation.id is None:
        return _IndexRepairState("MISSING")
    jobs = list(session.exec(
        select(RetrievalIndexJobModel)
        .where(
            RetrievalIndexJobModel.tenant_id == dataset.oid,
            RetrievalIndexJobModel.source_id == source.id,
            RetrievalIndexJobModel.target_generation == generation.generation,
        )
        .order_by(col(RetrievalIndexJobModel.id))
    ).all())
    job_ids = tuple(
        job.id for job in jobs if job.id is not None and job.status == "pending"
    )
    running_job_ids = tuple(
        job.id for job in jobs if job.id is not None and job.status == "running"
    )
    if (
        generation.status == "active"
        and source.active_generation == generation.generation
        and source.source_version == expected_source_version
    ):
        return _IndexRepairState("READY", generation, job_ids, running_job_ids)
    if generation.status in {"building", "ready"}:
        return _IndexRepairState(
            "IN_PROGRESS", generation, job_ids, running_job_ids
        )
    return _IndexRepairState("FAILED", generation, job_ids, running_job_ids)


def _has_current_index(
    session: Session,
    dataset: SemanticDataset,
    version: SemanticContractVersion,
) -> bool:
    """兼容调用方的布尔判断，真正状态由统一状态函数提供。"""

    return _index_repair_state(session, dataset, version).status == "READY"


def _candidate_state(
    session: Session,
    dataset: SemanticDataset,
) -> tuple[SemanticContractVersion | None, bool, bool]:
    """返回最新版本、是否需要发布、是否需要索引重试。"""

    latest = _latest_version(session, dataset)
    snapshot_valid = _has_valid_schema_snapshot(latest)
    # contract_version 清零表示存在未人工确认的草稿，不能由批量脚本自动发布。
    # 只有已经有发布指针但历史快照缺失/损坏时，才允许运维脚本修复发布。
    needs_publication = dataset.contract_version > 0 and not snapshot_valid
    needs_index = (
        latest is not None
        and snapshot_valid
        and dataset.contract_version > 0
        and not _has_current_index(session, dataset, latest)
    )
    return latest, needs_publication, needs_index


def find_datasets(session: Session, oid: int | None, dataset_id: int | None) -> list[SemanticDataset]:
    """找出已发布但当前历史最新版本缺少完整 Schema 的数据集。"""

    statement = select(SemanticDataset).where(SemanticDataset.status == 1)
    if oid is not None:
        statement = statement.where(SemanticDataset.oid == oid)
    if dataset_id is not None:
        statement = statement.where(SemanticDataset.id == dataset_id)
    datasets = list(session.exec(statement).all())
    history_statement = select(SemanticContractVersion.dataset_id)
    if oid is not None:
        history_statement = history_statement.where(
            SemanticContractVersion.oid == oid
        )
    history_dataset_ids = set(session.exec(history_statement).all())
    if dataset_id is not None and not datasets:
        explicit = session.get(SemanticDataset, dataset_id)
        if (
            explicit is not None
            and explicit.status == 1
            and (oid is None or explicit.oid == oid)
        ):
            datasets = [explicit]
    candidates: list[SemanticDataset] = []
    for dataset in datasets:
        if dataset.id is None:
            continue
        latest, needs_publication, needs_index = _candidate_state(session, dataset)
        # 批量模式只处理历史表中确实存在旧发布记录的数据集；显式指定时允许
        # 检查没有历史记录的数据集，以便运维人员得到明确的失败报告。
        if dataset_id is None and dataset.id not in history_dataset_ids:
            continue
        if needs_publication or needs_index:
            candidates.append(dataset)
    return candidates


def _dataset_summary(dataset: SemanticDataset) -> dict[str, Any]:
    return {
        "oid": dataset.oid,
        "dataset_id": dataset.id,
        "dataset_biz_name": dataset.biz_name,
        "current_contract_version": dataset.contract_version,
    }


def republish_dataset(
    session: Session,
    dataset: SemanticDataset,
    *,
    process_index: bool,
) -> dict[str, Any]:
    """重新校验、发布快照，并为发布后的 Schema 创建完整索引重建任务。"""

    if dataset.id is None:
        raise ValueError("SEMANTIC_DATASET_NOT_PERSISTED")
    latest, needs_publication, needs_index = _candidate_state(session, dataset)
    if dataset.contract_version <= 0:
        # 执行层无条件保护人工发布边界，防止并发修改后的草稿进入索引 generation。
        return {
            **_dataset_summary(dataset),
            "status": "SKIPPED",
            "publication_status": "SKIPPED",
            "contract_version": dataset.contract_version,
            "schema_fingerprint": "",
            "reason_codes": ["SEMANTIC_DATASET_MANUAL_PUBLICATION_REQUIRED"],
            "checks": [],
            "index_status": "SKIPPED",
            "index_state": "MISSING",
        }
    loader = SemanticSchemaLoader(session)
    report = None
    if needs_publication:
        report = SemanticContractPublicationService(
            SqlModelSemanticContractRepository(session)
        ).publish_dataset(
            dataset.oid,
            dataset.id,
            loader,
        )
        publication_status = report.status
        contract_version = report.contract_version
        schema_fingerprint = report.schema_fingerprint
        checks = [item.model_dump(mode="json") for item in report.checks]
    else:
        publication_status = "SKIPPED"
        contract_version = dataset.contract_version
        schema_fingerprint = latest.schema_fingerprint if latest is not None else ""
        checks = []
    if needs_publication:
        # 发布成功后重新读取版本，索引 source_version 必须与新快照一致。
        latest = _latest_version(session, dataset)
    result: dict[str, Any] = {
        **_dataset_summary(dataset),
        "status": "FAILED" if publication_status == "INVALID" else "READY",
        "publication_status": publication_status,
        "contract_version": contract_version,
        "schema_fingerprint": schema_fingerprint,
        "reason_codes": report.reason_codes if report is not None else [],
        "checks": checks,
        "index_status": "SKIPPED",
    }
    if publication_status == "INVALID":
        return result
    if latest is None:
        result["index_state"] = "MISSING"
        return result
    index_state = (
        _index_repair_state(session, dataset, latest)
        if _has_valid_schema_snapshot(latest)
        else _IndexRepairState("MISSING")
    )
    result["index_state"] = index_state.status
    if index_state.status == "READY":
        result["index_status"] = "READY"
        return result

    if index_state.status == "IN_PROGRESS":
        result.update(
            {
                "index_status": (
                    "processed"
                    if process_index and index_state.job_ids
                    else "queued"
                ),
                "retrieval_generation": index_state.generation.generation
                if index_state.generation is not None
                else None,
                "retrieval_job_ids": list(index_state.job_ids),
                "running_retrieval_job_ids": list(index_state.running_job_ids),
            }
        )
        if process_index and index_state.job_ids:
            try:
                job_results = process_index_jobs(index_state.job_ids)
                failed_jobs = [item for item in job_results if item.status == "failed"]
                if failed_jobs:
                    failed_job = failed_jobs[0]
                    result.update(
                        {
                            "status": "FAILED",
                            "index_status": "FAILED",
                            "index_state": "FAILED",
                            "index_error_code": failed_job.error_code
                            or "RETRIEVAL_INDEX_JOB_FAILED",
                        }
                    )
                else:
                    # worker 使用独立 Session，必须刷新当前 Session 后再读最终状态。
                    session.expire_all()
                    refreshed_state = _index_repair_state(session, dataset, latest)
                    result["index_state"] = refreshed_state.status
                    result["index_status"] = _index_status_from_state(
                        refreshed_state.status
                    )
                    result["retrieval_job_ids"] = list(refreshed_state.job_ids)
                    result["running_retrieval_job_ids"] = list(
                        refreshed_state.running_job_ids
                    )
            except (SemanticError, ValueError, SQLAlchemyError) as error:
                result.update(
                    {
                        "status": "FAILED",
                        "index_status": "FAILED",
                        "index_state": "FAILED",
                        "index_error_code": type(error).__name__,
                        "index_error_message": str(error),
                    }
                )
        return result

    index_result = SemanticDatasetIndexService(
        SqlModelDatasetIndexRepository(session),
        SemanticSchemaService(loader),
        SemanticIndexCoordinator(session),
    )
    # 发布服务已经在同一 Session 中提交；索引服务只负责新一代索引任务。
    try:
        rebuild = index_result.rebuild_index(dataset.oid, dataset.id)
    except (SemanticError, ValueError, SQLAlchemyError) as error:
        session.rollback()
        result.update(
            {
                "status": "FAILED",
                "index_status": "FAILED",
                "index_state": "FAILED",
                "index_error_code": type(error).__name__,
                "index_error_message": str(error),
            }
        )
        return result
    result.update(
        {
            "index_version": rebuild.index_version,
            "retrieval_generation": rebuild.generation,
            "retrieval_job_ids": list(rebuild.job_ids),
            "index_status": "processed" if process_index else "queued",
            "index_state": "IN_PROGRESS",
        }
    )
    if process_index:
        try:
            job_results = process_index_jobs(rebuild.job_ids)
            failed_jobs = [item for item in job_results if item.status == "failed"]
            if failed_jobs:
                failed_job = failed_jobs[0]
                result.update(
                    {
                        "status": "FAILED",
                        "index_status": "FAILED",
                        "index_state": "FAILED",
                        "index_error_code": failed_job.error_code
                        or "RETRIEVAL_INDEX_JOB_FAILED",
                    }
                )
            else:
                # 新 generation 处理后以数据库中的 active generation 为准。
                session.expire_all()
                refreshed_latest = _latest_version(session, dataset) or latest
                refreshed_state = _index_repair_state(
                    session, dataset, refreshed_latest
                )
                result["index_state"] = refreshed_state.status
                result["index_status"] = _index_status_from_state(
                    refreshed_state.status
                )
        except (SemanticError, ValueError, SQLAlchemyError) as error:
            result.update(
                {
                    "status": "FAILED",
                    "index_status": "FAILED",
                    "index_state": "FAILED",
                    "index_error_code": type(error).__name__,
                    "index_error_message": str(error),
                }
            )
    return result


def main() -> None:
    args = parse_args()
    if args.oid is not None and args.oid <= 0:
        raise ValueError("--oid 必须是正整数")
    if args.dataset_id is not None and args.dataset_id <= 0:
        raise ValueError("--dataset-id 必须是正整数")
    if args.process_index and not args.apply:
        raise ValueError("--process-index 必须与 --apply 一起使用")

    with Session(engine) as session:
        candidates = find_datasets(session, args.oid, args.dataset_id)
        report: dict[str, Any] = {
            "apply": args.apply,
            "process_index": args.process_index,
            "candidate_count": len(candidates),
            "datasets": [_dataset_summary(dataset) for dataset in candidates],
        }
        if args.apply:
            results: list[dict[str, Any]] = []
            for candidate in candidates:
                # 每个数据集独立事务，单个数据集失败时不会影响已经完成的批次。
                with Session(engine) as dataset_session:
                    dataset = dataset_session.get(SemanticDataset, candidate.id)
                    if dataset is None:
                        results.append(
                            {
                                "dataset_id": candidate.id,
                                "status": "FAILED",
                                "error_code": "SEMANTIC_DATASET_NOT_FOUND",
                                "error_message": "数据集不存在或已停用",
                                "checks": [],
                            }
                        )
                        continue
                    try:
                        results.append(
                            republish_dataset(
                                dataset_session,
                                dataset,
                                process_index=args.process_index,
                            )
                        )
                    except (SemanticError, ValueError, SQLAlchemyError) as error:
                        # 运维批处理必须保留单个数据集的异常，同时继续生成其余结果。
                        dataset_session.rollback()
                        results.append(
                            {
                                **_dataset_summary(dataset),
                                "status": "FAILED",
                                "error_code": type(error).__name__,
                                "error_message": str(error),
                                "checks": [],
                            }
                        )
            report["results"] = results
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
