from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from apps.semantic.errors import (
    SemanticDataAccessError,
    SemanticError,
    SemanticForbiddenError,
    SemanticNotFoundError,
    SemanticValidationError,
)


def semantic_error_to_http(error: SemanticError) -> HTTPException:
    if isinstance(error, SemanticNotFoundError):
        return HTTPException(status_code=404, detail=error.detail)
    if isinstance(error, SemanticForbiddenError):
        return HTTPException(status_code=403, detail=error.detail)
    if isinstance(error, SemanticValidationError):
        return HTTPException(status_code=400, detail=error.detail)
    if isinstance(error, SemanticDataAccessError):
        return HTTPException(status_code=500, detail=error.detail)
    raise TypeError(f"未注册的语义错误: {type(error).__name__}")


@contextmanager
def map_semantic_errors_to_http() -> Iterator[None]:
    try:
        yield
    except SemanticError as error:
        raise semantic_error_to_http(error) from error
