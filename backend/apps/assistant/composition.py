"""Assistant 依赖组装入口。"""

import secrets

from sqlmodel import Session

from apps.ai_model.reference import build_ai_model_reference_service
from apps.assistant.repository.external import AssistantOutDs
from apps.assistant.repository.sqlmodel import SQLModelAssistantRepository
from apps.assistant.services import AssistantService
from apps.datasource import build_datasource_catalog


def build_assistant_service(session: Session) -> AssistantService:
    model_service = build_ai_model_reference_service(session)
    return AssistantService(
        SQLModelAssistantRepository(session),
        build_datasource_catalog(session),
        model_exists=model_service.exists,
        generate_app_id=lambda: secrets.token_urlsafe(24),
        generate_app_secret=lambda: secrets.token_urlsafe(48),
        external_datasource_factory=AssistantOutDs,
    )
