"""Bearer 与 API Key 令牌校验入口。"""

import jwt
from jwt.exceptions import InvalidTokenError
from sqlmodel import Session

from apps.access_control.cache import get_api_key
from apps.access_control.composition import (
    build_api_key_service,
    build_authentication_service,
)
from apps.access_control.models.dto import UserInfoDTO
from common.core import security
from common.core.config import settings
from common.core.schemas import TokenPayload


def authenticate_bearer_token(session: Session, token: str) -> UserInfoDTO:
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[security.ALGORITHM],
    )
    token_data = TokenPayload(**payload)
    if token_data.id is None:
        raise InvalidTokenError("Missing user id payload")
    return build_authentication_service(session).require_active_user(token_data.id)


async def authenticate_api_key_token(
    session: Session,
    token: str,
) -> UserInfoDTO:
    unverified_payload = jwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=[security.ALGORITHM],
    )
    access_key = unverified_payload.get("access_key")
    if not isinstance(access_key, str) or not access_key:
        raise InvalidTokenError("Miss access_key payload error!")

    api_key = await get_api_key(session, access_key)
    api_key = build_api_key_service(session).require_active_api_key(api_key)
    jwt.decode(token, api_key.secret_key, algorithms=[security.ALGORITHM])
    return build_authentication_service(session).require_active_user(api_key.uid)
