import logging
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy.orm import scoped_session, sessionmaker

from common.core.db import engine

executor = ThreadPoolExecutor(max_workers=200)
session_maker = scoped_session(sessionmaker(bind=engine))
logger = logging.getLogger(__name__)


def _log_background_failure(future: Future[None]) -> None:
    error = future.exception()
    if error is not None:
        logger.error(
            "SQL 示例 embedding 后台任务失败",
            exc_info=(type(error), error, error.__traceback__),
        )


def run_save_data_training_embeddings(ids: list[int]) -> None:
    from apps.knowledge.repository.embedding import save_sql_example_embeddings

    future = executor.submit(save_sql_example_embeddings, session_maker, ids)
    future.add_done_callback(_log_background_failure)


def fill_empty_data_training_embeddings() -> None:
    from apps.knowledge.repository.embedding import (
        fill_missing_sql_example_embeddings,
    )

    future = executor.submit(fill_missing_sql_example_embeddings, session_maker)
    future.add_done_callback(_log_background_failure)
