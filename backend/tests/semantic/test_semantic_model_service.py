from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.semantic.api.models import build_model_schema
from apps.semantic.errors import (
    SemanticNotFoundError,
    SemanticValidationError,
)
from apps.semantic.models.dto import ModelBuildSchemaPayload, ModelRelationPayload
from apps.semantic.models.orm import SemanticModel
from apps.semantic.services.model_relation_service import (
    SemanticModelRelationService,
)
from apps.semantic.services.model_service import SemanticModelService


def test_build_model_schema_requires_columns_in_application_layer():
    payload = ModelBuildSchemaPayload(datasource_id=7)

    with pytest.raises(SemanticValidationError) as exc_info:
        SemanticModelService(
            _UnusedModelRepository(),
            _ActiveDomainRepository(),
            _ExistingDatasourceReader(),
        ).build_model_schema(oid=1, payload=payload)

    assert exc_info.value.detail == "SEMANTIC_MODEL_COLUMNS_REQUIRED"


def test_create_model_relation_maps_domain_mismatch_to_application_error():
    payload = ModelRelationPayload(
        domain_id=10,
        left_model_id=1,
        right_model_id=2,
    )
    model_repository = _ModelRepository(
        {
            1: _model(model_id=1, domain_id=10),
            2: _model(model_id=2, domain_id=11),
        }
    )

    with pytest.raises(SemanticValidationError) as exc_info:
        SemanticModelRelationService(
            _UnusedRelationRepository(),
            model_repository,
        ).create_model_relation(oid=1, payload=payload)

    assert exc_info.value.detail == "SEMANTIC_MODEL_RELATION_DOMAIN_MISMATCH"


def test_create_model_relation_hides_cross_tenant_model():
    payload = ModelRelationPayload(
        domain_id=10,
        left_model_id=1,
        right_model_id=2,
    )
    model_repository = _ModelRepository(
        {
            1: _model(model_id=1, domain_id=10),
            2: _model(model_id=2, domain_id=10, oid=2),
        }
    )

    with pytest.raises(SemanticNotFoundError) as exc_info:
        SemanticModelRelationService(
            _UnusedRelationRepository(),
            model_repository,
        ).create_model_relation(oid=1, payload=payload)

    assert exc_info.value.detail == "SEMANTIC_MODEL_NOT_FOUND"


@pytest.mark.anyio
async def test_build_model_schema_maps_semantic_error_to_http():
    payload = ModelBuildSchemaPayload(datasource_id=7)

    with pytest.raises(HTTPException) as exc_info:
        await build_model_schema(
            _ExistingDatasourceSession(), SimpleNamespace(oid=1), payload
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "SEMANTIC_MODEL_COLUMNS_REQUIRED"


class _ExistingDatasourceSession:
    def get(self, _model, _entity_id):
        return SimpleNamespace(oid=1)


class _ExistingDatasourceReader:
    def is_accessible(self, oid: int, datasource_id: int) -> bool:
        return oid == 1 and datasource_id == 7

    def list_columns(self, _datasource_id: int, _table_name: str):
        return []


class _ActiveDomainRepository:
    def is_active(self, _oid: int, _domain_id: int) -> bool:
        return True


class _UnusedModelRepository:
    pass


class _UnusedRelationRepository:
    pass


class _ModelRepository:
    def __init__(self, models: dict[int, SemanticModel]):
        self.models = models

    def get_active(self, oid: int, model_id: int):
        model = self.models.get(model_id)
        if model is None or model.oid != oid or model.status != 1:
            return None
        return model


def _model(model_id: int, domain_id: int, oid: int = 1) -> SemanticModel:
    return SemanticModel(
        id=model_id,
        oid=oid,
        domain_id=domain_id,
        datasource_id=7,
        name=f"模型{model_id}",
        biz_name=f"model_{model_id}",
    )
