from typing import Any, Protocol

from sqlalchemy import and_, text, update
from sqlmodel import col, select

from apps.ai_model.embedding import EmbeddingModelCache
from apps.knowledge.models.orm import SQLExampleModel
from common.core.config import settings
from common.utils.embedding_threads import run_save_data_training_embeddings

DATASOURCE_VECTOR_SQL = f"""
SELECT id
FROM (
    SELECT id, datasource, oid, enabled,
           (1 - (embedding <=> :embedding_array)) AS similarity
    FROM data_training
) AS candidate
WHERE similarity > {settings.EMBEDDING_DATA_TRAINING_SIMILARITY}
  AND oid = :oid
  AND datasource = :datasource
  AND enabled = true
ORDER BY similarity DESC
LIMIT {settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}
"""

ASSISTANT_VECTOR_SQL = f"""
SELECT id
FROM (
    SELECT id, advanced_application, oid, enabled,
           (1 - (embedding <=> :embedding_array)) AS similarity
    FROM data_training
) AS candidate
WHERE similarity > {settings.EMBEDDING_DATA_TRAINING_SIMILARITY}
  AND oid = :oid
  AND advanced_application = :advanced_application
  AND enabled = true
ORDER BY similarity DESC
LIMIT {settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}
"""


class LegacySQLExampleVectorSearch:
    """迁移期读取旧 data_training.embedding 的向量召回实现。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    def search_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        embedding = EmbeddingModelCache.get_model().embed_query(question)
        if assistant_id is not None:
            rows = self._session.execute(
                text(ASSISTANT_VECTOR_SQL),
                {
                    "embedding_array": str(embedding),
                    "oid": workspace_id,
                    "advanced_application": assistant_id,
                },
            )
        else:
            rows = self._session.execute(
                text(DATASOURCE_VECTOR_SQL),
                {
                    "embedding_array": str(embedding),
                    "oid": workspace_id,
                    "datasource": datasource_id,
                },
            )
        return [int(row.id) for row in rows]


class LegacySQLExampleIndexGateway:
    """迁移期把源数据变更提交给旧 embedding 后台任务。"""

    def enqueue_upserts(self, example_ids: list[int]) -> None:
        if settings.EMBEDDING_ENABLED and example_ids:
            run_save_data_training_embeddings(example_ids)

    def enqueue_deletes(self, example_ids: list[int]) -> None:
        # 旧向量与源记录同表，删除源记录即完成旧索引删除。
        _ = example_ids


class ScopedSessionFactory(Protocol):
    def __call__(self) -> Any: ...

    def remove(self) -> None: ...


def save_sql_example_embeddings(
    session_maker: ScopedSessionFactory,
    example_ids: list[int],
) -> None:
    """在一个事务内更新指定 SQL 示例的旧向量列。"""

    if not settings.EMBEDDING_ENABLED or not example_ids:
        return
    session = session_maker()
    try:
        rows = session.query(SQLExampleModel).filter(
            and_(col(SQLExampleModel.id).in_(example_ids))
        ).all()
        texts: list[str] = []
        for row in rows:
            if row.id is None or row.question is None:
                raise ValueError("SQL_EXAMPLE_EMBEDDING_SOURCE_INVALID")
            texts.append(row.question)

        embeddings = EmbeddingModelCache.get_model().embed_documents(texts)
        if len(embeddings) != len(rows):
            raise ValueError("SQL_EXAMPLE_EMBEDDING_COUNT_MISMATCH")
        for row, embedding in zip(rows, embeddings, strict=True):
            session.execute(
                update(SQLExampleModel)
                .where(SQLExampleModel.id == row.id)
                .values(embedding=embedding)
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session_maker.remove()


def fill_missing_sql_example_embeddings(
    session_maker: ScopedSessionFactory,
) -> None:
    """提交缺失旧向量的 SQL 示例，等待后续 Retrieval 投影替代。"""

    if not settings.EMBEDDING_ENABLED:
        return
    session = session_maker()
    try:
        example_ids = list(
            session.execute(
                select(SQLExampleModel.id).where(
                    col(SQLExampleModel.embedding).is_(None)
                )
            ).scalars().all()
        )
    finally:
        session_maker.remove()
    save_sql_example_embeddings(session_maker, example_ids)
