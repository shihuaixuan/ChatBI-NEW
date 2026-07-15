from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from sqlalchemy import delete, select, text

from apps.headless.models import (
    HeadlessAssetDocument,
    HeadlessAssetEmbedding,
    HeadlessDataSet,
    HeadlessMetric,
)
from apps.headless.service import HeadlessSchemaBuilder
from common.core.config import settings


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        """将文本转换为向量。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量将文本转换为向量，并保持输入顺序。"""


@dataclass
class StaticEmbeddingProvider:
    vector: list[float]
    provider: str = "static"
    model: str = "static-vector"

    @property
    def dimension(self) -> int:
        return len(self.vector)

    def embed_query(self, text: str) -> list[float]:
        # 测试专用 provider，避免单元测试访问外部网络。
        return list(self.vector)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        provider: str = "openai_compatible",
        timeout: float = 30.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.provider = provider
        self.timeout = timeout

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        if not vectors:
            raise ValueError("EMBEDDING_PROVIDER_EMPTY_RESULT")
        return vectors[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # 兼容硅基流动等 OpenAI embeddings 协议服务。
        response = httpx.post(
            f"{self.api_base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload["data"]
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("EMBEDDING_PROVIDER_BATCH_COUNT_MISMATCH")
        if all(isinstance(item, dict) and isinstance(item.get("index"), int) for item in data):
            data = sorted(data, key=lambda item: item["index"])
        return [
            [float(value) for value in item["embedding"]]
            for item in data
        ]


def default_metric_embedding_provider() -> EmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        api_base_url=settings.HEADLESS_METRIC_EMBEDDING_API_BASE_URL,
        api_key=settings.HEADLESS_METRIC_EMBEDDING_API_KEY,
        model=settings.HEADLESS_METRIC_EMBEDDING_MODEL,
        dimension=settings.HEADLESS_METRIC_EMBEDDING_DIMENSION,
        provider=settings.HEADLESS_METRIC_EMBEDDING_PROVIDER,
    )


def _clean_text(value) -> str:
    return " ".join(str(value or "").split())


def _unique_texts(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text_value = _clean_text(value)
        if text_value and text_value not in result:
            result.append(text_value)
    return result


def build_metric_embedding_text(metric: HeadlessMetric) -> str:
    lines: list[str] = []
    name = _clean_text(metric.name)
    aliases = _unique_texts(metric.alias or [])
    description = _clean_text(metric.description)

    if name:
        lines.append(f"指标名称: {name}")
    if aliases:
        lines.append(f"指标别名: {', '.join(aliases)}")
    if description:
        lines.append(f"指标说明: {description}")
    return "\n".join(lines)


def metric_embedding_text_hash(text_value: str) -> str:
    # 使用文本 hash 判断后续是否需要重建向量。
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _load_dataset_metric_ids(session, oid: int, dataset_id: int) -> set[int]:
    schema = HeadlessSchemaBuilder(session).build_dataset_schema(oid, dataset_id)
    return {metric.id for metric in schema.metrics}


def _load_metrics_for_dataset(session, oid: int, dataset_id: int) -> list[HeadlessMetric]:
    metric_ids = _load_dataset_metric_ids(session, oid, dataset_id)
    if not metric_ids:
        return []
    return list(
        session.exec(
            select(HeadlessMetric).where(
                HeadlessMetric.oid == oid,
                HeadlessMetric.id.in_(metric_ids),
                HeadlessMetric.status == 1,
            )
        )
        .scalars()
        .all()
    )


def _document_id_by_metric(session, oid: int, dataset_id: int) -> dict[int, int]:
    documents = list(
        session.exec(
            select(HeadlessAssetDocument).where(
                HeadlessAssetDocument.oid == oid,
                HeadlessAssetDocument.dataset_id == dataset_id,
                HeadlessAssetDocument.asset_type == "METRIC",
            )
        )
        .scalars()
        .all()
    )
    return {document.asset_id: document.id for document in documents if document.id is not None}


def _new_embedding_record(
    *,
    oid: int,
    dataset_id: int,
    metric: HeadlessMetric,
    document_id: int | None,
    embedding_text: str,
    provider: EmbeddingProvider,
    batch_id: str,
    status: str,
    embedding: list[float] | None = None,
    error_message: str | None = None,
) -> HeadlessAssetEmbedding:
    now = datetime.now()
    return HeadlessAssetEmbedding(
        oid=oid,
        dataset_id=dataset_id,
        asset_type="METRIC",
        asset_id=metric.id or 0,
        document_id=document_id,
        embedding_text=embedding_text,
        embedding_text_hash=metric_embedding_text_hash(embedding_text),
        embedding=embedding,
        embedding_provider=provider.provider,
        embedding_model=provider.model,
        embedding_dim=len(embedding) if embedding is not None else provider.dimension,
        embedding_batch_id=batch_id,
        status=status,
        error_message=error_message,
        created_at=now,
        updated_at=now,
    )


def rebuild_dataset_metric_embeddings(
    session,
    oid: int,
    dataset_id: int,
    provider: EmbeddingProvider | None = None,
) -> dict:
    dataset = session.get(HeadlessDataSet, dataset_id)
    if dataset is None or dataset.oid != oid or dataset.status != 1:
        raise ValueError("HEADLESS_DATASET_NOT_FOUND")

    provider = provider or default_metric_embedding_provider()
    batch_id = hashlib.sha256(f"{oid}:{dataset_id}:{datetime.now().isoformat()}".encode()).hexdigest()[:16]

    # 按产品语义，重建前删除当前数据集已有指标向量。
    session.exec(
        delete(HeadlessAssetEmbedding).where(
            HeadlessAssetEmbedding.oid == oid,
            HeadlessAssetEmbedding.dataset_id == dataset_id,
            HeadlessAssetEmbedding.asset_type == "METRIC",
        )
    )

    metrics = _load_metrics_for_dataset(session, oid, dataset_id)
    document_ids = _document_id_by_metric(session, oid, dataset_id)
    result = {
        "dataset_id": dataset_id,
        "asset_type": "METRIC",
        "deleted": True,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "batch_id": batch_id,
    }

    for metric in metrics:
        result["processed"] += 1
        embedding_text = build_metric_embedding_text(metric)
        if not embedding_text:
            session.add(
                _new_embedding_record(
                    oid=oid,
                    dataset_id=dataset_id,
                    metric=metric,
                    document_id=document_ids.get(metric.id),
                    embedding_text="",
                    provider=provider,
                    batch_id=batch_id,
                    status="FAILED",
                    error_message="EMPTY_METRIC_EMBEDDING_TEXT",
                )
            )
            result["failed"] += 1
            continue

        try:
            vector = provider.embed_query(embedding_text)
            session.add(
                _new_embedding_record(
                    oid=oid,
                    dataset_id=dataset_id,
                    metric=metric,
                    document_id=document_ids.get(metric.id),
                    embedding_text=embedding_text,
                    embedding=vector,
                    provider=provider,
                    batch_id=batch_id,
                    status="SUCCEEDED",
                )
            )
            result["succeeded"] += 1
        except Exception as exc:
            session.add(
                _new_embedding_record(
                    oid=oid,
                    dataset_id=dataset_id,
                    metric=metric,
                    document_id=document_ids.get(metric.id),
                    embedding_text=embedding_text,
                    provider=provider,
                    batch_id=batch_id,
                    status="FAILED",
                    error_message=str(exc),
                )
            )
            result["failed"] += 1

    session.commit()
    return result


def retrieve_metric_embedding_matches(
    session,
    oid: int,
    dataset_id: int,
    question: str,
    provider: EmbeddingProvider,
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    if session is None:
        return []
    vector = provider.embed_query(question)
    limit = top_k or settings.HEADLESS_METRIC_EMBEDDING_TOP_K
    rows = session.execute(
        text(
            """
            SELECT asset_id, (1 - (embedding <=> :embedding_array)) AS similarity
            FROM headless_asset_embedding
            WHERE oid = :oid
              AND dataset_id = :dataset_id
              AND asset_type = 'METRIC'
              AND status = 'SUCCEEDED'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> :embedding_array
            LIMIT :limit
            """
        ),
        {
            "embedding_array": str(vector),
            "oid": oid,
            "dataset_id": dataset_id,
            "limit": limit,
        },
    )
    return [(int(row[0]), float(row[1])) for row in rows]
