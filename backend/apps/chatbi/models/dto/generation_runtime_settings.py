from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationRuntimeSettingsData:
    """运行参数投影使用的当前默认值。"""

    assistant_name: str
    enable_sql_row_limit: bool
    history_round_limit: int


@dataclass(frozen=True, slots=True)
class GenerationRuntimeSettings:
    assistant_name: str
    enable_sql_row_limit: bool
    history_round_limit: int


__all__ = ["GenerationRuntimeSettings", "GenerationRuntimeSettingsData"]
