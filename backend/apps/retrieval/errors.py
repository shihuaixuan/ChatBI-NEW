"""统一检索错误语义。"""

from __future__ import annotations

from typing import Any, ClassVar


class RetrievalError(RuntimeError):
    """检索领域错误基类，向调用方提供稳定错误码和重试语义。"""

    code: ClassVar[str] = "RETRIEVAL_ERROR"
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class RetrievalConfigurationError(RetrievalError):
    """检索 profile 或 provider 配置无效。"""

    code = "RETRIEVAL_CONFIGURATION_ERROR"


class RetrievalProviderUnavailableError(RetrievalError):
    """Embedding 或 reranker provider 暂时不可用。"""

    code = "RETRIEVAL_PROVIDER_UNAVAILABLE"
    retryable = True


class RetrievalIndexUnavailableError(RetrievalError):
    """目标索引不存在、未激活或暂时不可读。"""

    code = "RETRIEVAL_INDEX_UNAVAILABLE"
    retryable = True


class RetrievalDimensionMismatchError(RetrievalError):
    """查询向量与索引 profile 的向量维度不一致。"""

    code = "RETRIEVAL_DIMENSION_MISMATCH"


class RetrievalQueryError(RetrievalError):
    """检索请求或查询规划结果不合法。"""

    code = "RETRIEVAL_QUERY_ERROR"


class RetrievalPermissionError(RetrievalError):
    """调用者无权访问目标检索 scope。"""

    code = "RETRIEVAL_PERMISSION_DENIED"
