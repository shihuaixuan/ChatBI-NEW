"""页面嵌入应用管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.assistant.composition import build_assistant_service
from apps.assistant.cors import update_dynamic_cors
from apps.assistant.models.dto import AssistantBase, AssistantDTO, AssistantRecord
from common.core.deps import SessionDep

router = APIRouter(
    tags=["system_embedded"],
    prefix="/system/embedded",
    include_in_schema=False,
)


class PageEmbeddedList(BaseModel):
    items: list[AssistantRecord]
    total: int
    page: int
    size: int


@router.get("/{page_num}/{page_size}")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def query(
    session: SessionDep,
    page_num: int,
    page_size: int,
    keyword: str | None = Query(default=None),
) -> PageEmbeddedList:
    items, total = build_assistant_service(session).list_page_embedded(
        page_num,
        page_size,
        keyword,
    )
    return PageEmbeddedList(
        items=items,
        total=total,
        page=page_num,
        size=page_size,
    )


@router.post("")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def add(
    request: Request,
    session: SessionDep,
    creator: AssistantBase,
) -> AssistantRecord:
    creator.type = 4
    result = build_assistant_service(session).create(creator, 1)
    update_dynamic_cors(request.app, build_assistant_service(session))
    return result


@router.put("")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def update(
    request: Request,
    session: SessionDep,
    editor: AssistantDTO,
) -> AssistantRecord:
    editor.type = 4
    result = build_assistant_service(session).update(editor, 1)
    update_dynamic_cors(request.app, build_assistant_service(session))
    return result


@router.patch("/secret/{id}")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def rotate_secret(session: SessionDep, id: int) -> AssistantRecord:
    return build_assistant_service(session).rotate_app_secret(id)


@router.get("/{id}")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def get_one(
    session: SessionDep,
    id: int,
) -> AssistantRecord:
    return build_assistant_service(session).get_for_workspace(id, 1)


@router.delete("")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def delete(
    request: Request,
    session: SessionDep,
    ids: list[int],
) -> None:
    service = build_assistant_service(session)
    for assistant_id in ids:
        service.delete(assistant_id, 1)
    update_dynamic_cors(request.app, service)


__all__ = ["router"]
