from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from apps.headless.models import HeadlessMetric
from common.core.config import settings


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        """将文本转换为向量。"""


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
        # 兼容硅基流动等 OpenAI embeddings 协议服务。
        response = httpx.post(
            f"{self.api_base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        vector = payload["data"][0]["embedding"]
        return [float(value) for value in vector]


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
