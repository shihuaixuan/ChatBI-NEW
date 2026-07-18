from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.semantic.api.terms import create_term
from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DatasetPayload, TermPayload
from apps.semantic.models.orm import SemanticDomain
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.services.dataset_service import (
    SemanticDatasetService,
)
from apps.semantic.services.domain_service import SemanticDomainService


def test_create_dataset_requires_active_domain_in_application_layer():
    payload = DatasetPayload(domain_id=9, name="经营数据集", biz_name="business")

    with pytest.raises(SemanticNotFoundError) as exc_info:
        SemanticDatasetService(
            _UnusedDatasetRepository(),
            _MissingDomainRepository(),
        ).create_dataset(oid=1, payload=payload)

    assert exc_info.value.detail == "SEMANTIC_DOMAIN_NOT_FOUND"


@pytest.mark.anyio
async def test_create_term_maps_missing_domain_to_http_not_found():
    payload = TermPayload(domain_id=9, name="有效订单")

    with pytest.raises(HTTPException) as exc_info:
        await create_term(_MissingDomainSession(), SimpleNamespace(oid=1), payload)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "SEMANTIC_DOMAIN_NOT_FOUND"


def test_delete_domain_keeps_cascade_operations_in_one_transaction():
    domain = SemanticDomain(id=3, oid=1, name="经营分析", biz_name="business")
    session = _DomainDeleteSession(domain)

    result = SemanticDomainService(
        SqlModelDomainRepository(session)
    ).delete_domain(oid=1, domain_id=3)

    assert result == {"id": 3, "deleted": True}
    assert session.exec_count == 6
    assert session.deleted == [domain]
    assert session.commit_count == 1


class _MissingDomainSession:
    def get(self, _model, _entity_id):
        return None


class _MissingDomainRepository:
    def is_active(self, _oid, _domain_id):
        return False


class _UnusedDatasetRepository:
    pass


class _DomainDeleteSession:
    def __init__(self, domain: SemanticDomain):
        self.domain = domain
        self.exec_count = 0
        self.deleted = []
        self.commit_count = 0

    def get(self, _model, _entity_id):
        return self.domain

    def exec(self, _statement):
        self.exec_count += 1
        if self.exec_count == 1:
            return _Result([11])
        if self.exec_count == 2:
            return _Result([21])
        return _Result([])

    def delete(self, entity):
        self.deleted.append(entity)

    def commit(self):
        self.commit_count += 1


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values
