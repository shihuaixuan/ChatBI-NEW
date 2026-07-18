from apps.ai_model.models.orm import AiModelDetail as OwnedAiModelDetail
from apps.system.models.system_model import (
    AiModelDetail as CompatibilityAiModelDetail,
)


def test_system_model_reexports_ai_model_owned_orm() -> None:
    assert CompatibilityAiModelDetail is OwnedAiModelDetail
    assert OwnedAiModelDetail.__tablename__ == "ai_model"
