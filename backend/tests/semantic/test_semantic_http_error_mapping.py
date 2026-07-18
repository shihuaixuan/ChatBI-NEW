import pytest

from apps.semantic.api.error_mapping import semantic_error_to_http
from apps.semantic.errors import (
    SemanticDataAccessError,
    SemanticError,
    SemanticForbiddenError,
    SemanticNotFoundError,
    SemanticValidationError,
)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (SemanticNotFoundError("NOT_FOUND"), 404),
        (SemanticForbiddenError("FORBIDDEN"), 403),
        (SemanticValidationError("INVALID"), 400),
        (SemanticDataAccessError("FAILED"), 500),
    ],
)
def test_semantic_error_to_http_preserves_category_and_detail(
    error, status_code
):
    http_error = semantic_error_to_http(error)

    assert http_error.status_code == status_code
    assert http_error.detail == error.detail


def test_semantic_error_to_http_rejects_unregistered_error_type():
    with pytest.raises(TypeError, match="SemanticError"):
        semantic_error_to_http(SemanticError("UNKNOWN"))
