# Author: Junjun
# Date: 2025/7/1
import asyncio
from datetime import timedelta

import jwt
import orjson
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.identity import authenticate, user_ws_options
from apps.access_control.models.dto import BaseUserDTO, UserInfoDTO
from apps.agent.schemas import AgentStartStreamRequest
from apps.agent.service import (
    AgentDatasourceNotAllowedError,
    AgentNotEnabledError,
    create_agent_start_stream,
)
from apps.chat.api.chat import create_chat
from apps.chat.models.chat_model import ChatStart, CreateChat, McpDs, McpQuestion
from apps.datasource.crud.datasource import get_datasource_list
from common.core import security
from common.core.config import settings
from common.core.deps import SessionDep, Trans
from common.core.schemas import Token, TokenPayload, XOAuth2PasswordBearer
from common.core.security import create_access_token

reusable_oauth2 = XOAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)

router = APIRouter(tags=["mcp"], prefix="/mcp")


# @router.post("/access_token", operation_id="access_token")
# def local_login(
#         session: SessionDep,
#         form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
# ) -> Token:
#     user = authenticate(session=session, account=form_data.username, password=form_data.password)
#     if not user:
#         raise HTTPException(status_code=400, detail="Incorrect account or password")
#     access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
#     user_dict = user.to_dict()
#     return Token(access_token=create_access_token(
#         user_dict, expires_delta=access_token_expires
#     ))


def get_user(session: SessionDep, token: str) -> UserInfoDTO:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    session_user = (
        build_identity_workspace_service(session).get_user_info(token_data.id)
        if token_data.id is not None
        else None
    )
    if not session_user:
        raise HTTPException(status_code=404, detail="User not found")

    # MCP 当前保持固定中文环境，不改变原有语言行为。
    session_user.language = "zh-CN"
    if session_user.status != 1:
        raise HTTPException(status_code=400, detail="Inactive user")
    return session_user


@router.post("/mcp_start", operation_id="mcp_start")
async def mcp_start(session: SessionDep, chat: ChatStart):
    user: BaseUserDTO = authenticate(session=session, account=chat.username, password=chat.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect account or password")

    if not user.oid or user.oid == 0:
        raise HTTPException(status_code=400, detail="No associated workspace, Please contact the administrator")
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    user_dict = user.to_dict()
    t = Token(access_token=create_access_token(
        user_dict, expires_delta=access_token_expires
    ))
    c = create_chat(session, user, CreateChat(origin=1), False)
    return {"access_token": t.access_token, "chat_id": c.id}


@router.post("/mcp_ws_list", operation_id="mcp_ws_list")
async def ws_list(session: SessionDep, trans: Trans, token: str):
    session_user = get_user(session, token)
    return await user_ws_options(session, session_user.id, trans)


@router.post("/mcp_ds_list", operation_id="mcp_datasource_list")
async def datasource_list(session: SessionDep, mcp_ds: McpDs):
    session_user = get_user(session, mcp_ds.token)
    if mcp_ds.oid:
        session_user.oid = int(mcp_ds.oid)
    ds_list = get_datasource_list(session=session, user=session_user)
    result = []
    for item in ds_list:
        dic = item.__dict__
        dic.pop('embedding', None)
        dic.pop('table_relation', None)
        dic.pop('recommended_config', None)
        dic.pop('configuration', None)
        result.append(dic)
    return result


#
#
# @router.get("/model_list", operation_id="get_model_list")
# async def get_model_list(session: SessionDep):
#     return session.query(AiModelDetail).all()


@router.post("/mcp_question", operation_id="mcp_question")
async def mcp_question(session: SessionDep, chat: McpQuestion):
    session_user = get_user(session, chat.token)
    lang = chat.lang
    if lang in ["zh-CN", "zh-TW", "en", "ko-KR"]:
        session_user.language = lang
    if chat.oid:
        session_user.oid = int(chat.oid)
    ds_id: int | None = None
    if chat.datasource_id:
        if isinstance(chat.datasource_id, str):
            if chat.datasource_id.strip() == "":
                ds_id = None
            else:
                try:
                    ds_id = int(chat.datasource_id.strip())
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid datasource ID")
        elif isinstance(chat.datasource_id, int):
            ds_id = chat.datasource_id
        else:
            raise HTTPException(status_code=400, detail="Invalid datasource ID")

    request = AgentStartStreamRequest(
        action="start",
        chat_id=chat.chat_id,
        question=chat.question,
        datasource_id=ds_id,
    )
    try:
        events = create_agent_start_stream(session_user, request)
    except (AgentNotEnabledError, AgentDatasourceNotAllowedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if chat.stream:
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        return await asyncio.to_thread(_collect_agent_result, events)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _collect_agent_result(events) -> dict:
    """将 Agent SSE 收敛为 MCP 非流式响应。"""

    for frame in events:
        payload = orjson.loads(frame.removeprefix("data:").strip())
        event_type = payload.get("type")
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        if event_type == "clarification":
            return {
                "success": True,
                "status": "waiting_user",
                "record_id": payload.get("record_id"),
                "run_id": payload.get("run_id"),
                "clarification": content,
            }
        if event_type == "finish":
            return {
                "success": True,
                "status": "finished",
                "record_id": payload.get("record_id"),
                "run_id": payload.get("run_id"),
                "content": content.get("content"),
            }
        if event_type == "error":
            raise RuntimeError(str(content.get("content") or "AGENT_EXECUTION_FAILED"))
    raise RuntimeError("AGENT_STREAM_FINISHED_WITHOUT_TERMINAL_EVENT")
