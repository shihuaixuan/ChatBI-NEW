"""Semantic 统一检索索引任务的运行时消费者。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor

from sqlmodel import Session

from apps.retrieval.embedding import (
    EmbeddingProvider,
    default_retrieval_embedding_provider,
)
from apps.retrieval.semantic_indexing import build_semantic_index_profile
from apps.retrieval.indexing import IndexJobRunResult, RetrievalIndexingService
from common.core.db import engine

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="semantic-index")


def process_semantic_index_jobs(
    job_ids: Sequence[int] | None = None,
    *,
    provider: EmbeddingProvider | None = None,
) -> list[IndexJobRunResult]:
    """处理指定任务；未指定时持续领取当前 profile 的全部 pending 任务。"""

    embedding_provider = provider or default_retrieval_embedding_provider()
    profile = build_semantic_index_profile()
    results: list[IndexJobRunResult] = []
    if job_ids is not None:
        # 每个任务独立提交，单个未知异常不会回滚已经完成的 generation 进度。
        for job_id in dict.fromkeys(job_ids):
            with Session(engine) as session:
                result = RetrievalIndexingService(session, profile).process_job(job_id, embedding_provider)
                session.commit()
                results.append(result)
        return results

    while True:
        with Session(engine) as session:
            next_result = RetrievalIndexingService(session, profile).process_next(embedding_provider)
            if next_result is None:
                session.rollback()
                return results
            session.commit()
            results.append(next_result)


def submit_pending_semantic_index_jobs() -> Future[list[IndexJobRunResult]]:
    """应用启动时异步恢复 durable pending 任务，避免阻塞服务就绪。"""

    future = _executor.submit(process_semantic_index_jobs)
    future.add_done_callback(_log_worker_failure)
    return future


def _log_worker_failure(future: Future[list[IndexJobRunResult]]) -> None:
    error = future.exception()
    if error is not None:
        logger.error(
            "Semantic 统一索引 worker 执行失败",
            exc_info=(type(error), error, error.__traceback__),
        )


__all__ = ["process_semantic_index_jobs", "submit_pending_semantic_index_jobs"]
