"""Assistant 令牌身份校验测试。"""

from unittest.mock import Mock

import jwt
import pytest

from apps.assistant.errors import AssistantTokenError
from apps.assistant.models.dto import AssistantRecord
from apps.assistant.token_authentication import authenticate_embedded_token
from common.core import security


def _embedded_record() -> AssistantRecord:
    return AssistantRecord(
        id=10,
        name="页面嵌入",
        domain="https://example.com",
        type=4,
        configuration=None,
        description=None,
        oid=1,
        enable_custom_model=False,
        custom_model=None,
        create_time=1,
        app_id="expected-app",
        app_secret="assistant-token-secret-at-least-32-bytes",
    )


def test_embedded_token_app_id_must_match_selected_assistant(
    monkeypatch,
) -> None:
    service = Mock()
    service.get.return_value = _embedded_record()
    monkeypatch.setattr(
        "apps.assistant.token_authentication.build_assistant_service",
        lambda session: service,
    )
    token = jwt.encode(
        {
            "embeddedId": 10,
            "appId": "another-app",
            "account": "member",
        },
        "assistant-token-secret-at-least-32-bytes",
        algorithm=security.ALGORITHM,
    )

    with pytest.raises(AssistantTokenError, match="APP_ID_MISMATCH"):
        authenticate_embedded_token(Mock(), token)
