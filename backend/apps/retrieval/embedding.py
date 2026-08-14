from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx

from common.core.config import settings


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        """将查询文本转换为向量。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量将文档文本转换为向量，并保持输入顺序。"""


@dataclass
class StaticEmbeddingProvider:
    vector: list[float]
    provider: str = "static"
    model: str = "static-vector"

    @property
    def dimension(self) -> int:
        return len(self.vector)

    def embed_query(self, text: str) -> list[float]:
        # 测试专用 Provider，避免单元测试访问外部网络。
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
        return [[float(value) for value in item["embedding"]] for item in data]


class SentenceTransformerEmbeddingProvider:
    """使用本地 sentence-transformers 模型生成同一向量空间的查询和文档向量。"""

    def __init__(self, model: str, dimension: int) -> None:
        self.provider = "sentence_transformers"
        self.model = model
        self.dimension = dimension

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        if not vectors:
            raise ValueError("EMBEDDING_PROVIDER_EMPTY_RESULT")
        return vectors[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = _sentence_transformer_model(self.model)
        encoded = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=min(64, len(texts)),
        )
        vectors = encoded.tolist()
        if not isinstance(vectors, list):
            raise ValueError("EMBEDDING_PROVIDER_RESPONSE_INVALID")
        return [[float(value) for value in vector] for vector in vectors]


@lru_cache(maxsize=4)
def _sentence_transformer_model(model_name: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "sentence_transformers provider 需要安装 sentence-transformers 依赖"
        ) from exc
    try:
        return SentenceTransformer(
            model_name,
            local_files_only=settings.RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY,
        )
    except OSError as exc:
        mode = "本地缓存" if settings.RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY else "模型目录"
        raise RuntimeError(
            f"检索 embedding 模型无法从{mode}加载: {model_name}。"
            "请先准备模型文件，或关闭 RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY。"
        ) from exc


def preload_retrieval_embedding_model() -> None:
    """应用启动时预加载本地检索模型，避免首个用户请求承担冷启动耗时。"""

    if not settings.RETRIEVAL_EMBEDDING_ENABLED:
        return
    if settings.RETRIEVAL_EMBEDDING_PROVIDER != "sentence_transformers":
        return
    _sentence_transformer_model(settings.RETRIEVAL_EMBEDDING_MODEL)


def default_retrieval_embedding_provider() -> EmbeddingProvider:
    """构造统一检索索引和查询共用的 Embedding Provider。"""

    provider = settings.RETRIEVAL_EMBEDDING_PROVIDER.strip()
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            api_base_url=settings.RETRIEVAL_EMBEDDING_API_BASE_URL,
            api_key=settings.RETRIEVAL_EMBEDDING_API_KEY,
            model=settings.RETRIEVAL_EMBEDDING_MODEL,
            dimension=settings.RETRIEVAL_EMBEDDING_DIMENSION,
            provider=provider,
        )
    if provider == "sentence_transformers":
        return SentenceTransformerEmbeddingProvider(
            model=settings.RETRIEVAL_EMBEDDING_MODEL,
            dimension=settings.RETRIEVAL_EMBEDDING_DIMENSION,
        )
    raise ValueError(f"RETRIEVAL_EMBEDDING_PROVIDER_UNSUPPORTED:{provider}")


__all__ = [
    "EmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "SentenceTransformerEmbeddingProvider",
    "StaticEmbeddingProvider",
    "default_retrieval_embedding_provider",
    "preload_retrieval_embedding_model",
]
