from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from apps.ai_model.errors import (
    AIModelDefaultCannotDeleteError,
    AIModelDefaultChangeRequiresEndpointError,
)
from apps.ai_model.models.dto import AiModelConfigItem, AiModelCreator
from apps.ai_model.repository.sqlmodel import SQLModelAIModelManagementRepository
from apps.ai_model.services import (
    AIModelManagementService,
    AIModelSecretMigrationService,
)


async def _identity_decrypt(value: str) -> str:
    return value


def _creator(
    name: str,
    *,
    default_model: bool = False,
    supplier: int = 1,
) -> AiModelCreator:
    return AiModelCreator(
        name=name,
        model_type=0,
        base_model=f"{name}-base",
        supplier=supplier,
        protocol=1,
        default_model=default_model,
        api_domain="https://example.test/v1",
        api_key="plain-key",
        config_list=[AiModelConfigItem(key="temperature", val="1", name="温度")],
    )


def _build_service() -> tuple[Session, AIModelManagementService]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.tables["ai_model"].create(engine)
    session = Session(engine)
    service = AIModelManagementService(
        SQLModelAIModelManagementRepository(session),
        _identity_decrypt,
    )
    return session, service


def test_create_and_switch_default_model_preserves_single_default() -> None:
    session, service = _build_service()
    try:
        first = service.create_model(_creator("模型一"))
        second = service.create_model(_creator("模型二", default_model=True))

        models = service.list_models()

        assert first.default_model is True
        assert second.default_model is True
        assert [model.id for model in models if model.default_model] == [second.id]
    finally:
        session.close()


def test_normal_update_cannot_change_default_model() -> None:
    session, service = _build_service()
    try:
        created = service.create_model(_creator("模型一"))
        editor = asyncio.run(service.get_model(created.id))
        editor.default_model = False

        with pytest.raises(AIModelDefaultChangeRequiresEndpointError):
            service.update_model(editor)
    finally:
        session.close()


def test_default_model_cannot_be_deleted() -> None:
    session, service = _build_service()
    try:
        created = service.create_model(_creator("模型一"))

        with pytest.raises(AIModelDefaultCannotDeleteError, match="模型一"):
            service.delete_model(created.id)
    finally:
        session.close()


def test_secret_migration_encrypts_plain_values_and_updates_supplier() -> None:
    session, service = _build_service()
    try:
        created = service.create_model(_creator("模型一", supplier=12))
        repository = SQLModelAIModelManagementRepository(session)

        async def encrypt(value: str) -> str:
            return f"encrypted:{value}"

        migrated_count = asyncio.run(
            AIModelSecretMigrationService(repository, encrypt).migrate()
        )
        migrated = repository.get_model(created.id)

        assert migrated_count == 1
        assert migrated is not None
        assert migrated.api_domain == "encrypted:https://example.test/v1"
        assert migrated.api_key == "encrypted:plain-key"
        assert migrated.supplier == 15
    finally:
        session.close()
