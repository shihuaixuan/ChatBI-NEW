from apps.chatbi.models import (
    GenerationRuntimeSettings,
    GenerationRuntimeSettingsData,
)
from apps.chatbi.services import GenerationRuntimeSettingsService


def test_runtime_settings_service_normalizes_values():
    result = GenerationRuntimeSettingsService().project(
        GenerationRuntimeSettingsData("Numora", False, 5),
        {
            "chat.sqlbot_name": " SQLBot ",
            "chat.limit_rows": " TRUE ",
            "chat.context_record_count": "-2",
        },
    )

    assert result.assistant_name == " SQLBot "
    assert result.enable_sql_row_limit is True
    assert result.history_round_limit == 0


def test_runtime_settings_service_keeps_defaults_for_missing_values():
    defaults = GenerationRuntimeSettingsData("Numora", True, 5)

    assert GenerationRuntimeSettingsService().project(defaults, {}) == (
        GenerationRuntimeSettings("Numora", True, 5)
    )
