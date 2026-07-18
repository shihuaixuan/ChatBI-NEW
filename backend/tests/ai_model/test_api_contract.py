from fastapi.routing import APIRoute

from apps.ai_model.models.dto import AiModelEditor as OwnedAiModelEditor
from apps.api import api_router
from apps.system.schemas.ai_model_schema import (
    AiModelEditor as CompatibilityAiModelEditor,
)


def test_model_routes_are_owned_by_ai_model_domain() -> None:
    routes = {
        (route.path, tuple(sorted(route.methods))): route.endpoint.__module__
        for route in api_router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/system/aimodel")
    }

    assert routes == {
        ("/system/aimodel/status", ("POST",)): "apps.ai_model.api.model_config",
        ("/system/aimodel/default", ("GET",)): "apps.ai_model.api.model_config",
        ("/system/aimodel/default/{id}", ("PUT",)): "apps.ai_model.api.model_config",
        ("/system/aimodel", ("GET",)): "apps.ai_model.api.model_config",
        ("/system/aimodel/{id}", ("GET",)): "apps.ai_model.api.model_config",
        ("/system/aimodel", ("POST",)): "apps.ai_model.api.model_config",
        ("/system/aimodel", ("PUT",)): "apps.ai_model.api.model_config",
        ("/system/aimodel/{id}", ("DELETE",)): "apps.ai_model.api.model_config",
    }


def test_system_schema_reexports_ai_model_owned_dto() -> None:
    assert CompatibilityAiModelEditor is OwnedAiModelEditor
