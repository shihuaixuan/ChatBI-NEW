from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import scoped_session, sessionmaker

from common.core.db import engine

executor = ThreadPoolExecutor(max_workers=200)
session_maker = scoped_session(sessionmaker(bind=engine))


def run_save_data_training_embeddings(ids: list[int]):
    from apps.data_training.curd.data_training import save_embeddings
    executor.submit(save_embeddings, session_maker, ids)


def fill_empty_data_training_embeddings():
    from apps.data_training.curd.data_training import run_fill_empty_embeddings
    executor.submit(run_fill_empty_embeddings, session_maker)
