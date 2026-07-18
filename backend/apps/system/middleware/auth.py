
import base64
from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session
from starlette.middleware.base import BaseHTTPMiddleware

from apps.access_control.composition import build_authentication_service
from apps.access_control.errors import (
    ApiKeyDisabledError,
    ApiKeyNotFoundError,
    UserInactiveError,
    UserNotFoundError,
    UserWorkspaceRequiredError,
)
from apps.access_control.identity import get_user_by_account
from apps.access_control.token_authentication import (
    authenticate_api_key_token,
    authenticate_bearer_token,
)
from apps.system.crud.assistant import get_assistant_info, get_assistant_user
from apps.system.models.system_model import AssistantModel
from apps.system.schemas.system_schema import AssistantHeader
from common.core import security
from common.core.config import settings
from common.core.db import engine
from common.core.deps import get_i18n
from common.core.schemas import TokenPayload
from common.utils.locale import I18n
from common.utils.utils import SQLBotLogUtil, get_origin_from_referer
from common.utils.whitelist import whiteUtils


class TokenMiddleware(BaseHTTPMiddleware):
    
    
    
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        
        if self.is_options(request) or whiteUtils.is_whitelisted(request.url.path):
            return await call_next(request)
        assistantTokenKey = settings.ASSISTANT_TOKEN_KEY
        assistantToken = request.headers.get(assistantTokenKey)
        askToken = request.headers.get("X-SQLBOT-ASK-TOKEN")
        trans = await get_i18n(request)
        if askToken:
            validate_pass, data = await self.validateAskToken(askToken, trans)
            if validate_pass:
                request.state.current_user = data
                return await call_next(request)
            message = trans('i18n_permission.authenticate_invalid', msg = data)
            return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
        #if assistantToken and assistantToken.lower().startswith("assistant "):
        if assistantToken:
            validator: tuple[any] = await self.validateAssistant(assistantToken, trans)
            if validator[0]:
                request.state.current_user = validator[1]
                if request.state.current_user and trans.lang:
                    request.state.current_user.language = trans.lang
                request.state.assistant = validator[2]
                origin = request.headers.get("X-SQLBOT-HOST-ORIGIN") or get_origin_from_referer(request)
                if origin and validator[2]:
                    request.state.assistant.request_origin = origin
                return await call_next(request)
            message = trans('i18n_permission.authenticate_invalid', msg = validator[1])
            return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
        #validate pass
        tokenkey = settings.TOKEN_KEY
        token = request.headers.get(tokenkey)
        validate_pass, data = await self.validateToken(token, trans)
        if validate_pass:
            request.state.current_user = data
            return await call_next(request)
        
        message = trans('i18n_permission.authenticate_invalid', msg = data)
        return JSONResponse(message, status_code=401, headers={"Access-Control-Allow-Origin": "*"})
    
    def is_options(self, request: Request):
        return request.method == "OPTIONS"
    
    async def validateAskToken(self, askToken: Optional[str], trans: I18n):
        if not askToken:
            return False, f"Miss Token[X-SQLBOT-ASK-TOKEN]!"
        schema, param = get_authorization_scheme_param(askToken)
        if schema.lower() != "sk":
            return False, f"Token schema error!"
        try:
            with Session(engine) as session:
                return True, await authenticate_api_key_token(session, param)
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
    
    async def validateToken(self, token: Optional[str], trans: I18n):
        if not token:
            return False, f"Miss Token[{settings.TOKEN_KEY}]!"
        schema, param = get_authorization_scheme_param(token)
        if schema.lower() != "bearer":
            return False, f"Token schema error!"
        try:
            with Session(engine) as session:
                return True, authenticate_bearer_token(session, param)
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

    @staticmethod
    def authentication_error_message(exc: Exception, trans: I18n):
        if isinstance(exc, ApiKeyNotFoundError):
            return "Invalid access_key!"
        if isinstance(exc, ApiKeyDisabledError):
            return "Disabled access_key!"
        if isinstance(exc, UserNotFoundError):
            return trans("i18n_not_exist", msg=trans("i18n_user.account"))
        if isinstance(exc, UserInactiveError):
            return trans(
                "i18n_login.user_disable",
                msg=trans("i18n_concat_admin"),
            )
        if isinstance(exc, UserWorkspaceRequiredError):
            return trans(
                "i18n_login.no_associated_ws",
                msg=trans("i18n_concat_admin"),
            )
        return exc
            
    
    async def validateAssistant(self, assistantToken: Optional[str], trans: I18n) -> tuple[any]:
        if not assistantToken:
            return False, f"Miss Token[{settings.TOKEN_KEY}]!"
        schema, param = get_authorization_scheme_param(assistantToken)
        
        
        try:
            if schema.lower() == 'embedded':
                return await self.validateEmbedded(param, trans)
            if schema.lower() != "assistant":
                return False, f"Token schema error!" 
            payload = jwt.decode(
                param, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
            )
            token_data = TokenPayload(**payload)
            if not payload['assistant_id']:
                return False, f"Miss assistant payload error!"
            with Session(engine) as session:
                """ session_user = await get_user_info(session = session, user_id = token_data.id)
                session_user = UserInfoDTO.model_validate(session_user) """
                session_user = get_assistant_user(id = token_data.id)
                assistant_info = await get_assistant_info(session=session, assistant_id=payload['assistant_id'])
                assistant_info = AssistantModel.model_validate(assistant_info)
                assistant_info = AssistantHeader.model_validate(assistant_info.model_dump(exclude_unset=True))
                session_user.oid = int(assistant_info.oid)
                        
                return True, session_user, assistant_info
        except Exception as e:
            SQLBotLogUtil.exception(f"Assistant validation error: {str(e)}")
            # Return False and the exception message
            return False, e
    
    async def validateEmbedded(self, param: str, trans: I18n) -> tuple[any]:
        try: 
            # WARNING: Signature verification is disabled for embedded tokens
            # This is a security risk and should only be used if absolutely necessary
            # Consider implementing proper signature verification with a shared secret
            payload: dict = jwt.decode(
                param,
                options={"verify_signature": False, "verify_exp": False},
                algorithms=[security.ALGORITHM]
            )
            app_key = payload.get('appId', '')
            embeddedId = payload.get('embeddedId', None)
            if not embeddedId:
                embeddedId = xor_decrypt(app_key)
            if not payload['account']:
                return False, f"Miss account payload error!"
            account = payload['account']
            with Session(engine) as session:
                assistant_info = await get_assistant_info(session=session, assistant_id=embeddedId)
                assistant_info = AssistantModel.model_validate(assistant_info)
                payload = jwt.decode(
                    param, assistant_info.app_secret, algorithms=[security.ALGORITHM]
                )
                assistant_info = AssistantHeader.model_validate(assistant_info.model_dump(exclude_unset=True))
                """ session_user = await get_user_info(session = session, user_id = token_data.id)
                session_user = UserInfoDTO.model_validate(session_user) """
                session_user = get_user_by_account(session = session, account=account)
                if not session_user:
                    message = trans('i18n_not_exist', msg = trans('i18n_user.account'))
                    raise Exception(message)
                session_user = build_authentication_service(session).require_active_user(
                    session_user.id
                )
                if session_user.oid:
                    assistant_info.oid = int(session_user.oid)
                return True, session_user, assistant_info
        except Exception as e:
            SQLBotLogUtil.exception(f"Embedded validation error: {str(e)}")
            # Return False and the exception message
            return False, e
    
def xor_decrypt(encrypted_str: str, key: int = 0xABCD1234) -> int:
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_str)
    hex_str = encrypted_bytes.hex()
    encrypted_num = int(hex_str, 16)
    return encrypted_num ^ key
