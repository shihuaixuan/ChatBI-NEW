from apps.ai_model.models.orm import AiModelDetail


def test_ai_model_orm_is_owned_by_ai_model_domain() -> None:
    assert AiModelDetail.__tablename__ == "ai_model"
    assert AiModelDetail.__module__ == "apps.ai_model.models.orm.model_config"
