from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.semantic.api.dimensions import create_dimension
from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import DimensionPayload, MetricPayload
from apps.semantic.services.metric_service import SemanticMetricService


def test_create_metric_requires_active_model_in_application_layer():
    payload = MetricPayload(model_id=9, name="销售额", biz_name="sales_amount")

    with pytest.raises(SemanticNotFoundError) as exc_info:
        SemanticMetricService(
            _UnusedMetricRepository(),
            _MissingModelReader(),
        ).create_metric(oid=1, payload=payload)

    assert exc_info.value.detail == "SEMANTIC_MODEL_NOT_FOUND"


@pytest.mark.anyio
async def test_create_dimension_maps_missing_model_to_http_not_found():
    payload = DimensionPayload(model_id=9, name="区域", biz_name="region")

    with pytest.raises(HTTPException) as exc_info:
        await create_dimension(_MissingModelSession(), SimpleNamespace(oid=1), payload)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "SEMANTIC_MODEL_NOT_FOUND"


class _MissingModelSession:
    def get(self, _model, _entity_id):
        return None


class _MissingModelReader:
    def get_active(self, _oid: int, _model_id: int):
        return None


class _UnusedMetricRepository:
    pass
