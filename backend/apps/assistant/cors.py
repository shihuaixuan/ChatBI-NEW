"""把 Assistant 允许域名同步到接口中间件。"""

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from apps.assistant.services import AssistantService
from common.core.config import settings
from common.core.response_middleware import ResponseMiddleware


def update_dynamic_cors(app: FastAPI, service: AssistantService) -> None:
    assistant_origins = service.list_allowed_origins()
    updated_origins = list(dict.fromkeys(settings.all_cors_origins + assistant_origins))

    cors_middleware = next(
        (
            middleware
            for middleware in app.user_middleware
            if middleware.cls == CORSMiddleware
        ),
        None,
    )
    if cors_middleware:
        cors_middleware.kwargs["allow_origins"] = updated_origins

    for instance in ResponseMiddleware.instances:
        instance.update_allow_origins(updated_origins)
