"""统一检索索引 durable job 的运行时消费者。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor

from sqlmodel import Session

from apps.retrieval.embedding import (
    EmbeddingProvider,
    default_retrieval_embedding_provider,
)
from apps.retrieval.index_profile import build_retrieval_index_profile
from apps.retrieval.indexing import IndexJobRunResult, RetrievalIndexingService
from common.core.db import engine

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="retrieval-index")


def process_index_jobs(
    job_ids: Sequence[int] | None = None,
    *,
    provider: EmbeddingProvider | None = None,
) -> list[IndexJobRunResult]:
    """处理指定任务；未指定时持续领取当前 profile 的全部 pending 任务。"""

    embedding_provider = provider or default_retrieval_embedding_provider()
    profile = build_retrieval_index_profile()
    results: list[IndexJobRunResult] = []
    if job_ids is not None:
        # 每个任务独立提交，单个未知异常不会回滚已经完成的 generation 进度。
        for job_id in dict.fromkeys(job_ids):
            with Session(engine) as session:
                result = RetrievalIndexingService(session, profile).process_job(
                    job_id,
                    embedding_provider,
                )
                session.commit()
                results.append(result)
        return results

    while True:
        with Session(engine) as session:
            next_result = RetrievalIndexingService(session, profile).process_next(
                embedding_provider
            )
            if next_result is None:
                session.rollback()
                return results
            session.commit()
            results.append(next_result)


def submit_index_jobs(job_ids: Sequence[int]) -> Future[list[IndexJobRunResult]]:
    """源事务提交后异步处理本次新建的 durable job。"""

    future = _executor.submit(process_index_jobs, tuple(job_ids))
    future.add_done_callback(_log_worker_failure)
    return future


def submit_pending_index_jobs() -> Future[list[IndexJobRunResult]]:
    """应用启动时异步恢复全部 durable pending 任务。"""

    future = _executor.submit(process_index_jobs)
    future.add_done_callback(_log_worker_failure)
    return future


def _log_worker_failure(future: Future[list[IndexJobRunResult]]) -> None:
    error = future.exception()
    if error is not None:
        logger.error(
            "统一检索索引 worker 执行失败",
            exc_info=(type(error), error, error.__traceback__),
        )


__all__ = ["process_index_jobs", "submit_index_jobs", "submit_pending_index_jobs"]
