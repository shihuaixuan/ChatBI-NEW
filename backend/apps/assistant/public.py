"""Assistant 供其他领域和兼容入口使用的公开能力。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from sqlmodel import Session

from apps.assistant.composition import build_assistant_service
from apps.assistant.cors import update_dynamic_cors
from apps.assistant.errors import AssistantNotFoundError
from apps.assistant.models.dto import (
    AssistantBase,
    AssistantHeader,
    AssistantRecord,
    AssistantReference,
)
from apps.assistant.repository.external import AssistantOutDs, AssistantOutDsFactory
from apps.assistant.token_authentication import create_assistant_user
from apps.datasource import (
    ExternalDatasource as AssistantOutDsSchema,
)
from apps.datasource import build_external_datasource_configuration
from common.core.cache_keys import CacheName, CacheNamespace
from common.core.db import engine
from common.core.sqlbot_cache import cache


@cache(
    namespace=CacheNamespace.EMBEDDED_INFO,
    cacheName=CacheName.ASSISTANT_INFO,
    keyExpression="assistant_id",
)
async def get_assistant_info(
    *,
    session: Session,
    assistant_id: int,
) -> AssistantRecord | None:
    try:
        return build_assistant_service(session).get(assistant_id)
    except AssistantNotFoundError:
        return None


def get_assistant_user(*, id: int):
    return create_assistant_user(id)


def get_assistant_ds(session: Session, llm_service) -> list[dict[str, object]]:
    service = build_assistant_service(session)
    assistant: AssistantHeader = llm_service.current_assistant
    if assistant.type in {1, 3}:
        external_catalog = service.build_external_datasource_catalog(assistant)
        llm_service.out_ds_instance = external_catalog
        return external_catalog.get_simple_ds_list()
    return [item.model_dump() for item in service.list_datasources(assistant)]


def get_out_ds_conf(
    datasource: AssistantOutDsSchema,
    timeout: int = 30,
) -> str:
    return build_external_datasource_configuration(datasource, timeout)


def list_assistant_references(
    assistant_ids: list[int] | None = None,
    *,
    workspace_id: int | None = None,
    assistant_type: int | None = None,
) -> list[AssistantReference]:
    with Session(engine) as session:
        return build_assistant_service(session).list_references(
            assistant_ids,
            workspace_id=workspace_id,
            assistant_type=assistant_type,
        )


def dynamic_upgrade_cors(request: Request, session: Session) -> None:
    update_dynamic_cors(request.app, build_assistant_service(session))


def init_dynamic_cors(app: FastAPI) -> None:
    with Session(engine) as session:
        update_dynamic_cors(app, build_assistant_service(session))


async def save(
    request: Request,
    session: Session,
    creator: AssistantBase,
    oid: int = 1,
) -> AssistantRecord:
    assistant = build_assistant_service(session).create(creator, oid)
    dynamic_upgrade_cors(request, session)
    return assistant


__all__ = [
    "AssistantOutDs",
    "AssistantOutDsFactory",
    "AssistantOutDsSchema",
    "dynamic_upgrade_cors",
    "get_assistant_ds",
    "get_assistant_info",
    "get_assistant_user",
    "get_out_ds_conf",
    "init_dynamic_cors",
    "list_assistant_references",
    "save",
]
