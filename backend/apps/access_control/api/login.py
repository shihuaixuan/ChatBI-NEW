"""本地登录与退出接口。"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from apps.access_control.composition import build_authentication_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_login_http_error
from apps.access_control.models.dto import LogoutDTO
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.config import settings
from common.core.deps import SessionDep, Trans
from common.core.schemas import Token
from common.core.security import create_access_token

router = APIRouter(tags=["login"], prefix="/login")


@router.post("/access-token")
@system_log(
    LogConfig(
        operation_type=OperationType.LOGIN,
        module=OperationModules.USER,
        result_id_expr="id",
    )
)
async def local_login(
    session: SessionDep,
    trans: Trans,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    try:
        user = build_authentication_service(session).authenticate_local(
            form_data.username,
            form_data.password,
        )
    except AccessControlError as exc:
        raise_login_http_error(exc, trans)

    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=create_access_token(user.to_dict(), expires_delta=expires)
    )


@router.post("/logout")
async def logout(session: SessionDep, request: Request, dto: LogoutDTO):
    del session, request, dto
    return None
