"""Semantic 检索源的运行配置、provider 观测与安全投影。"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.retrieval.embedding import EmbeddingProvider
from apps.retrieval.errors import (
    RetrievalConfigurationError,
    RetrievalDimensionMismatchError,
    RetrievalError,
    RetrievalIndexUnavailableError,
    RetrievalProviderUnavailableError,
)
from apps.retrieval.semantic_projector import (
    SemanticSourceProjector as SemanticSourceProjector,
)
from apps.retrieval.schemas import RetrievalChannelStatus


@dataclass(frozen=True, slots=True)
class RetrievalEmbeddingRuntimeConfig:
    """统一检索向量通道的显式运行配置。"""

    enabled: bool
    provider: str
    api_base_url: str
    api_key: str
    model: str
    dimension: int
    top_k: int
    allow_lexical_fallback: bool = True

    @classmethod
    def from_settings(cls, runtime_settings: Any) -> RetrievalEmbeddingRuntimeConfig:
        """从应用配置构造稳定快照，避免请求中途读取到不同配置。"""

        return cls(
            enabled=runtime_settings.RETRIEVAL_EMBEDDING_ENABLED,
            provider=runtime_settings.RETRIEVAL_EMBEDDING_PROVIDER,
            api_base_url=runtime_settings.RETRIEVAL_EMBEDDING_API_BASE_URL,
            api_key=runtime_settings.RETRIEVAL_EMBEDDING_API_KEY,
            model=runtime_settings.RETRIEVAL_EMBEDDING_MODEL,
            dimension=runtime_settings.RETRIEVAL_EMBEDDING_DIMENSION,
            top_k=runtime_settings.RETRIEVAL_EMBEDDING_TOP_K,
            allow_lexical_fallback=runtime_settings.RETRIEVAL_EMBEDDING_ALLOW_LEXICAL_FALLBACK,
        )

    def validate(self) -> None:
        """检查 provider 建立请求所需的全部配置。"""

        if not self.enabled:
            return
        if not self.provider.strip():
            self._raise_configuration_error("EMBEDDING_PROVIDER_MISSING", "检索 embedding provider 未配置")
        if self.provider == "openai_compatible":
            parsed_url = urlparse(self.api_base_url.strip())
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                self._raise_configuration_error("EMBEDDING_API_URL_INVALID", "检索 embedding API URL 无效")
            if not self.api_key.strip():
                self._raise_configuration_error("EMBEDDING_API_KEY_MISSING", "检索 embedding API key 未配置")
        elif self.provider != "sentence_transformers":
            self._raise_configuration_error(
                "EMBEDDING_PROVIDER_UNSUPPORTED",
                f"不支持的 embedding provider: {self.provider}",
            )
        if not self.model.strip():
            self._raise_configuration_error("EMBEDDING_MODEL_MISSING", "检索 embedding model 未配置")
        if self.dimension <= 0:
            self._raise_configuration_error("EMBEDDING_DIMENSION_INVALID", "检索 embedding 维度必须大于 0")
        if self.top_k <= 0:
            self._raise_configuration_error("EMBEDDING_TOP_K_INVALID", "检索 embedding top_k 必须大于 0")

    @staticmethod
    def _raise_configuration_error(reason_code: str, message: str) -> None:
        raise RetrievalConfigurationError(message, details={"reason_code": reason_code})


@dataclass(slots=True)
class DenseChannelTracker:
    """一次统一检索请求内的 dense 通道状态汇总。"""

    status: RetrievalChannelStatus = RetrievalChannelStatus.SKIPPED
    latency_ms: float = 0
    candidate_count: int = 0
    error_code: str | None = None

    def record_success(self, *, latency_ms: float, candidate_count: int) -> None:
        self.latency_ms += max(latency_ms, 0)
        self.candidate_count += max(candidate_count, 0)
        if self.status not in {RetrievalChannelStatus.UNAVAILABLE, RetrievalChannelStatus.FAILED}:
            self.status = RetrievalChannelStatus.SUCCEEDED
            self.error_code = None

    def record_error(self, error: RetrievalError, *, latency_ms: float = 0) -> None:
        self.latency_ms += max(latency_ms, 0)
        self.status = (
            RetrievalChannelStatus.UNAVAILABLE
            if isinstance(error, (RetrievalConfigurationError, RetrievalIndexUnavailableError))
            else RetrievalChannelStatus.FAILED
        )
        self.error_code = str(error.details.get("reason_code") or error.code)


class ObservedEmbeddingProvider:
    """把已知 provider 故障转换为检索领域错误，并校验向量维度。"""

    def __init__(self, delegate: EmbeddingProvider, expected_dimension: int) -> None:
        self._delegate = delegate
        self.provider = delegate.provider
        self.model = delegate.model
        self.dimension = expected_dimension

    def embed_query(self, text: str) -> list[float]:
        started = perf_counter()
        try:
            vector = self._delegate.embed_query(text)
        except httpx.TimeoutException as exc:
            raise RetrievalProviderUnavailableError(
                "检索 embedding provider 请求超时",
                details={
                    "reason_code": "EMBEDDING_PROVIDER_TIMEOUT",
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise RetrievalProviderUnavailableError(
                "检索 embedding provider 请求失败",
                details={
                    "reason_code": "EMBEDDING_PROVIDER_REQUEST_FAILED",
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            ) from exc
        if len(vector) != self.dimension:
            raise RetrievalDimensionMismatchError(
                "查询向量维度与索引配置不一致",
                details={
                    "reason_code": "EMBEDDING_DIMENSION_MISMATCH",
                    "expected_dimension": self.dimension,
                    "actual_dimension": len(vector),
                },
            )
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        started = perf_counter()
        try:
            vectors = self._delegate.embed_documents(texts)
        except httpx.TimeoutException as exc:
            raise RetrievalProviderUnavailableError(
                "检索 embedding provider 批量请求超时",
                details={
                    "reason_code": "EMBEDDING_PROVIDER_TIMEOUT",
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise RetrievalProviderUnavailableError(
                "检索 embedding provider 批量请求失败",
                details={
                    "reason_code": "EMBEDDING_PROVIDER_REQUEST_FAILED",
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            ) from exc
        if len(vectors) != len(texts):
            raise RetrievalProviderUnavailableError(
                "检索 embedding provider 批量结果数量不一致",
                details={
                    "reason_code": "EMBEDDING_BATCH_COUNT_MISMATCH",
                    "expected_count": len(texts),
                    "actual_count": len(vectors),
                },
            )
        for vector in vectors:
            if len(vector) != self.dimension:
                raise RetrievalDimensionMismatchError(
                    "批量向量维度与索引配置不一致",
                    details={
                        "reason_code": "EMBEDDING_DIMENSION_MISMATCH",
                        "expected_dimension": self.dimension,
                        "actual_dimension": len(vector),
                    },
                )
        return vectors
