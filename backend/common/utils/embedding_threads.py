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


def run_save_table_embeddings(ids: list[int]):
    from apps.datasource.crud.table import save_table_embedding
    executor.submit(save_table_embedding, session_maker, ids)


def run_save_ds_embeddings(ids: list[int]):
    from apps.datasource.crud.table import save_ds_embedding
    executor.submit(save_ds_embedding, session_maker, ids)


def fill_empty_table_and_ds_embeddings():
    from apps.datasource.crud.table import run_fill_empty_table_and_ds_embedding
    executor.submit(run_fill_empty_table_and_ds_embedding, session_maker)
