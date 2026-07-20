"""旧 Chat 生成流程运行参数的解析规则（纯函数）。"""

from collections.abc import Mapping

from apps.chatbi.models.dto.generation_runtime_settings import (
    GenerationRuntimeSettings,
    GenerationRuntimeSettingsData,
)


def resolve_runtime_settings(
    defaults: GenerationRuntimeSettingsData,
    values: Mapping[str, str | None],
) -> GenerationRuntimeSettings:
    """统一解析旧 Chat 生成流程使用的运行参数。"""

    assistant_name = values.get("chat.sqlbot_name")
    row_limit = values.get("chat.limit_rows")
    history_limit = values.get("chat.context_record_count")

    normalized_name = defaults.assistant_name
    if assistant_name is not None and assistant_name.strip():
        normalized_name = assistant_name

    normalized_row_limit = defaults.enable_sql_row_limit
    if row_limit is not None:
        normalized_row_limit = row_limit.lower().strip() == "true"

    normalized_history_limit = defaults.history_round_limit
    if history_limit is not None:
        normalized_history_limit = max(int(history_limit), 0)

    return GenerationRuntimeSettings(
        assistant_name=normalized_name,
        enable_sql_row_limit=normalized_row_limit,
        history_round_limit=normalized_history_limit,
    )


__all__ = ["resolve_runtime_settings"]
