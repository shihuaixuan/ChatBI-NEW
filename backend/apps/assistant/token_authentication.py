"""Assistant 与 Embedded 令牌认证。"""

from __future__ import annotations

import base64

import jwt
from sqlmodel import Session

from apps.access_control.composition import build_authentication_service
from apps.access_control.identity import get_user_by_account
from apps.access_control.models.dto import UserInfoDTO
from apps.assistant.composition import build_assistant_service
from apps.assistant.errors import AssistantTokenError
from apps.assistant.models.dto import AssistantHeader
from common.core import security
from common.core.config import settings
from common.core.schemas import TokenPayload


def authenticate_assistant_token(
    session: Session,
    token: str,
) -> tuple[UserInfoDTO, AssistantHeader]:
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[security.ALGORITHM],
    )
    token_data = TokenPayload.model_validate(payload)
    assistant_id = payload.get("assistant_id")
    if assistant_id is None:
        raise AssistantTokenError("ASSISTANT_ID_REQUIRED")
    if token_data.id is None:
        raise AssistantTokenError("USER_ID_REQUIRED")

    assistant = build_assistant_service(session).get_header(int(assistant_id))
    user = create_assistant_user(token_data.id)
    user.oid = int(assistant.oid or 1)
    return user, assistant


def authenticate_embedded_token(
    session: Session,
    token: str,
) -> tuple[UserInfoDTO, AssistantHeader]:
    # 首次解码只用于选择应用密钥，随后必须使用该密钥完整验签。
    unverified_payload = jwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=[security.ALGORITHM],
    )
    app_id = unverified_payload.get("appId")
    embedded_id = unverified_payload.get("embeddedId")
    if embedded_id is None:
        if not isinstance(app_id, str) or not app_id:
            raise AssistantTokenError("APP_ID_REQUIRED")
        embedded_id = decrypt_embedded_id(app_id)

    assistant_record = build_assistant_service(session).get(int(embedded_id))
    if not assistant_record.app_secret:
        raise AssistantTokenError("APP_SECRET_REQUIRED")
    verified_payload = jwt.decode(
        token,
        assistant_record.app_secret,
        algorithms=[security.ALGORITHM],
    )
    verified_app_id = verified_payload.get("appId")
    if not assistant_record.app_id:
        raise AssistantTokenError("APP_ID_REQUIRED_ON_RECORD")
    if verified_app_id != assistant_record.app_id:
        raise AssistantTokenError("APP_ID_MISMATCH")
    account = verified_payload.get("account")
    if not isinstance(account, str) or not account:
        raise AssistantTokenError("ACCOUNT_REQUIRED")

    user_record = get_user_by_account(session=session, account=account)
    if user_record is None:
        raise AssistantTokenError("ACCOUNT_NOT_FOUND")
    user = build_authentication_service(session).require_active_user(user_record.id)
    assistant = AssistantHeader.model_validate(assistant_record.model_dump())
    if user.oid:
        assistant.oid = int(user.oid)
    return user, assistant


def create_assistant_user(user_id: int) -> UserInfoDTO:
    return UserInfoDTO(
        id=user_id,
        account="sqlbot-inner-assistant",
        oid=1,
        name="sqlbot-inner-assistant",
        email="sqlbot-inner-assistant@sqlbot.com",
    )


def decrypt_embedded_id(encrypted: str, key: int = 0xABCD1234) -> int:
    try:
        encrypted_bytes = base64.urlsafe_b64decode(encrypted)
        encrypted_number = int(encrypted_bytes.hex(), 16)
    except (ValueError, TypeError) as exc:
        raise AssistantTokenError("APP_ID_INVALID") from exc
    return encrypted_number ^ key
