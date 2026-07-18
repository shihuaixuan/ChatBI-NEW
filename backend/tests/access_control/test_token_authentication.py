"""Bearer 与 API Key 令牌校验测试。"""

import asyncio

import jwt
import pytest
from jwt.exceptions import InvalidTokenError

import apps.access_control.token_authentication as token_module
from apps.access_control.errors import ApiKeyDisabledError
from apps.access_control.models.dto import ApiKeyRecord, UserInfoDTO
from common.core import security
from common.core.config import settings


def _user() -> UserInfoDTO:
    return UserInfoDTO(
        id=2,
        account="member",
        oid=7,
        name="Member",
        email="member@example.com",
        status=1,
        origin=0,
    )


class _AuthenticationService:
    def require_active_user(self, user_id: int) -> UserInfoDTO:
        assert user_id == 2
        return _user()


class _ApiKeyService:
    @staticmethod
    def require_active_api_key(api_key: ApiKeyRecord | None) -> ApiKeyRecord:
        if api_key is None or not api_key.status:
            raise ApiKeyDisabledError("access-key")
        return api_key


def test_bearer_token_uses_signed_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        token_module,
        "build_authentication_service",
        lambda session: _AuthenticationService(),
    )
    token = jwt.encode(
        {"id": 2},
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )

    user = token_module.authenticate_bearer_token(MockSession(), token)

    assert user.id == 2


def test_bearer_token_requires_user_id() -> None:
    token = jwt.encode(
        {"account": "member"},
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        token_module.authenticate_bearer_token(MockSession(), token)


def test_api_key_token_is_verified_with_stored_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = ApiKeyRecord(
        id=11,
        uid=2,
        access_key="access-key",
        secret_key="secret-key-at-least-32-characters-long",
        status=True,
        create_time=1,
    )

    async def get_api_key(_session: object, access_key: str) -> ApiKeyRecord:
        assert access_key == "access-key"
        return api_key

    monkeypatch.setattr(token_module, "get_api_key", get_api_key)
    monkeypatch.setattr(
        token_module,
        "build_api_key_service",
        lambda session: _ApiKeyService(),
    )
    monkeypatch.setattr(
        token_module,
        "build_authentication_service",
        lambda session: _AuthenticationService(),
    )
    token = jwt.encode(
        {"access_key": "access-key"},
        "secret-key-at-least-32-characters-long",
        algorithm=security.ALGORITHM,
    )

    user = asyncio.run(
        token_module.authenticate_api_key_token(MockSession(), token)
    )

    assert user.id == 2


class MockSession:
    """令牌测试不访问数据库，只用作组装入口参数。"""
