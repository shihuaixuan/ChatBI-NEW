"""统一请求令牌认证中间件。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from apps.access_control.errors import (
    ApiKeyDisabledError,
    ApiKeyNotFoundError,
    UserInactiveError,
    UserNotFoundError,
    UserWorkspaceRequiredError,
)
from apps.access_control.token_authentication import (
    authenticate_api_key_token,
    authenticate_bearer_token,
)
from apps.assistant.errors import AssistantError, AssistantTokenError
from apps.assistant.token_authentication import (
    authenticate_assistant_token,
    authenticate_embedded_token,
    decrypt_embedded_id,
)
from common.core.config import settings
from common.core.db import engine
from common.core.deps import get_i18n
from common.utils.locale import I18nHelper
from common.utils.utils import SQLBotLogUtil, get_origin_from_referer
from common.utils.whitelist import whiteUtils

AuthenticationFailure = tuple[bool, object]
AssistantAuthenticationResult = tuple[bool, object, object | None]
CallNext = Callable[[Request], Awaitable[Response]]


class TokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: CallNext,
    ) -> Response:
        if request.method == "OPTIONS" or whiteUtils.is_whitelisted(request.url.path):
            return await call_next(request)

        assistant_token = request.headers.get(settings.ASSISTANT_TOKEN_KEY)
        ask_token = request.headers.get("X-SQLBOT-ASK-TOKEN")
        trans = await get_i18n(request)
        if ask_token:
            valid, data = await self.validateAskToken(ask_token, trans)
            if valid:
                request.state.current_user = data
                return await call_next(request)
            message = trans("i18n_permission.authenticate_invalid", msg=data)
            return JSONResponse(
                message,
                status_code=401,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        if assistant_token:
            valid, data, assistant = await self.validateAssistant(
                assistant_token,
                trans,
            )
            if valid:
                request.state.current_user = data
                if request.state.current_user and trans.lang:
                    request.state.current_user.language = trans.lang
                request.state.assistant = assistant
                origin = request.headers.get(
                    "X-SQLBOT-HOST-ORIGIN"
                ) or get_origin_from_referer(request)
                if origin and assistant:
                    request.state.assistant.request_origin = origin
                return await call_next(request)
            message = trans("i18n_permission.authenticate_invalid", msg=data)
            return JSONResponse(
                message,
                status_code=401,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        token = request.headers.get(settings.TOKEN_KEY)
        valid, data = await self.validateToken(token, trans)
        if valid:
            request.state.current_user = data
            return await call_next(request)

        message = trans("i18n_permission.authenticate_invalid", msg=data)
        return JSONResponse(
            message,
            status_code=401,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def validateAskToken(
        self,
        ask_token: str | None,
        trans: I18nHelper,
    ) -> AuthenticationFailure:
        if not ask_token:
            return False, "Miss Token[X-SQLBOT-ASK-TOKEN]!"
        scheme, parameter = get_authorization_scheme_param(ask_token)
        if scheme.lower() != "sk":
            return False, "Token schema error!"
        try:
            with Session(engine) as session:
                return True, await authenticate_api_key_token(session, parameter)
        except jwt.ExpiredSignatureError:
            return False, jwt.ExpiredSignatureError(
                trans("i18n_permission.token_expired")
            )
        except (
            InvalidTokenError,
            ValidationError,
            ApiKeyNotFoundError,
            ApiKeyDisabledError,
            UserNotFoundError,
            UserInactiveError,
            UserWorkspaceRequiredError,
        ) as exc:
            SQLBotLogUtil.exception(f"Token validation error: {exc}")
            return False, self.authentication_error_message(exc, trans)

    async def validateToken(
        self,
        token: str | None,
        trans: I18nHelper,
    ) -> AuthenticationFailure:
        if not token:
            return False, f"Miss Token[{settings.TOKEN_KEY}]!"
        scheme, parameter = get_authorization_scheme_param(token)
        if scheme.lower() != "bearer":
            return False, "Token schema error!"
        try:
            with Session(engine) as session:
                return True, authenticate_bearer_token(session, parameter)
        except jwt.ExpiredSignatureError:
            return False, jwt.ExpiredSignatureError(
                trans("i18n_permission.token_expired")
            )
        except (
            InvalidTokenError,
            ValidationError,
            UserNotFoundError,
            UserInactiveError,
            UserWorkspaceRequiredError,
        ) as exc:
            SQLBotLogUtil.exception(f"Token validation error: {exc}")
            return False, self.authentication_error_message(exc, trans)

    async def validateAssistant(
        self,
        assistant_token: str | None,
        trans: I18nHelper,
    ) -> AssistantAuthenticationResult:
        if not assistant_token:
            return False, f"Miss Token[{settings.ASSISTANT_TOKEN_KEY}]!", None
        scheme, parameter = get_authorization_scheme_param(assistant_token)
        if scheme.lower() not in {"assistant", "embedded"}:
            return False, "Token schema error!", None
        try:
            with Session(engine) as session:
                if scheme.lower() == "embedded":
                    user, assistant = authenticate_embedded_token(session, parameter)
                else:
                    user, assistant = authenticate_assistant_token(session, parameter)
            return True, user, assistant
        except jwt.ExpiredSignatureError:
            return (
                False,
                jwt.ExpiredSignatureError(trans("i18n_permission.token_expired")),
                None,
            )
        except (
            InvalidTokenError,
            ValidationError,
            AssistantError,
            UserNotFoundError,
            UserInactiveError,
            UserWorkspaceRequiredError,
        ) as exc:
            SQLBotLogUtil.exception(f"Assistant validation error: {exc}")
            return False, self.authentication_error_message(exc, trans), None

    async def validateEmbedded(
        self,
        parameter: str,
        trans: I18nHelper,
    ) -> AssistantAuthenticationResult:
        return await self.validateAssistant(f"Embedded {parameter}", trans)

    @staticmethod
    def authentication_error_message(
        exc: Exception,
        trans: I18nHelper,
    ) -> object:
        if isinstance(exc, ApiKeyNotFoundError):
            return "Invalid access_key!"
        if isinstance(exc, ApiKeyDisabledError):
            return "Disabled access_key!"
        if isinstance(exc, UserNotFoundError):
            return trans("i18n_not_exist", msg=trans("i18n_user.account"))
        if isinstance(exc, UserInactiveError):
            return trans("i18n_login.user_disable", msg=trans("i18n_concat_admin"))
        if isinstance(exc, UserWorkspaceRequiredError):
            return trans("i18n_login.no_associated_ws", msg=trans("i18n_concat_admin"))
        if isinstance(exc, AssistantTokenError) and str(exc) == "ACCOUNT_NOT_FOUND":
            return trans("i18n_not_exist", msg=trans("i18n_user.account"))
        return exc


def xor_decrypt(encrypted_str: str, key: int = 0xABCD1234) -> int:
    """旧函数名兼容转发。"""

    return decrypt_embedded_id(encrypted_str, key)
