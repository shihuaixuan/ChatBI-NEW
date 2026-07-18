import pytest
from fastapi import HTTPException

from apps.api import api_router
from apps.semantic.api.legacy_terms import _raise_excel_contract_required
from common.audit.models.log_model import OperationModules
from common.audit.schemas.logger_decorator import (
    get_resource_name_by_id_and_module,
)


def test_application_router_exposes_semantic_legacy_terminology_contracts():
    routes = {(route.path, tuple(sorted(route.methods))) for route in api_router.routes}

    assert (
        "/system/terminology/page/{current_page}/{page_size}",
        ("GET",),
    ) in routes
    assert ("/system/terminology", ("PUT",)) in routes
    assert ("/system/terminology", ("DELETE",)) in routes
    assert ("/system/terminology/{id}/enable/{enabled}", ("GET",)) in routes


def test_legacy_terminology_excel_contract_is_explicitly_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _raise_excel_contract_required()

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "SEMANTIC_TERM_EXCEL_CONTRACT_REQUIRED"


def test_terminology_audit_resource_uses_semantic_term_table():
    session = _QueryCaptureSession()

    result = get_resource_name_by_id_and_module(
        session,
        [7],
        OperationModules.TERMINOLOGY,
    )

    assert result == []
    assert "FROM headless_term" in str(session.query)


class _QueryCaptureSession:
    def __init__(self):
        self.query = None

    def execute(self, query):
        self.query = query
        return self

    def fetchall(self):
        return []
