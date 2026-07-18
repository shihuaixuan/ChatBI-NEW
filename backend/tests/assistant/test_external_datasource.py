"""Assistant 外部数据源适配测试。"""

import json
from unittest.mock import Mock

import pytest

from apps.assistant.errors import AssistantExternalDatasourceError
from apps.assistant.models.dto import AssistantHeader
from apps.assistant.repository.external import AssistantOutDs


def _assistant() -> AssistantHeader:
    return AssistantHeader(
        id=10,
        name="高级助手",
        domain="https://example.com",
        type=1,
        configuration=json.dumps({"endpoint": "/datasources", "timeout": 5}),
        certificate="[]",
    )


def test_relative_external_endpoint_uses_configured_domain(monkeypatch) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "code": 0,
        "data": [{"id": 1, "name": "销售库", "type": "mysql"}],
    }
    request_get = Mock(return_value=response)
    monkeypatch.setattr(
        "apps.assistant.repository.external.http_datasource.requests.get",
        request_get,
    )

    catalog = AssistantOutDs(_assistant())

    assert catalog.ds_list[0].name == "销售库"
    assert request_get.call_args.kwargs["url"] == "https://example.com/datasources"
    assert request_get.call_args.kwargs["timeout"] == 5


def test_external_api_business_error_is_not_converted_to_empty_list(
    monkeypatch,
) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"code": 500, "message": "upstream failed"}
    monkeypatch.setattr(
        "apps.assistant.repository.external.http_datasource.requests.get",
        Mock(return_value=response),
    )

    with pytest.raises(AssistantExternalDatasourceError, match="upstream failed"):
        AssistantOutDs(_assistant())
