from common.core.config import Settings


def test_semantic_settings_defaults_keep_retrieval_disabled_and_scoped():
    settings = Settings()

    assert settings.SEMANTIC_LAYER_ENABLED is False
    assert settings.SEMANTIC_APPROVED_ONLY is True
    assert settings.SEMANTIC_METRIC_TOP_K == 5
    assert settings.SEMANTIC_DIMENSION_TOP_K == 8
    assert settings.SEMANTIC_SEARCH_TIMEOUT_MS == 800


def test_semantic_settings_can_be_overridden_by_environment_style_values():
    settings = Settings(
        SEMANTIC_LAYER_ENABLED="true",
        SEMANTIC_APPROVED_ONLY="false",
        SEMANTIC_METRIC_TOP_K=3,
        SEMANTIC_DIMENSION_TOP_K=4,
        SEMANTIC_SEARCH_TIMEOUT_MS=1200,
    )

    assert settings.SEMANTIC_LAYER_ENABLED is True
    assert settings.SEMANTIC_APPROVED_ONLY is False
    assert settings.SEMANTIC_METRIC_TOP_K == 3
    assert settings.SEMANTIC_DIMENSION_TOP_K == 4
    assert settings.SEMANTIC_SEARCH_TIMEOUT_MS == 1200
