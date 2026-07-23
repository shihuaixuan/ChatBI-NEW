from dataclasses import dataclass, field
from typing import Any

from apps.chatbi.models.dto.streaming import ModelMessage


@dataclass(frozen=True, slots=True)
class GenerationHistoryLog:
    """生成日志中的稳定历史消息快照。"""

    record_id: int | None
    messages: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class GenerationHistoryProjectionData:
    """SQL 与图表历史消息投影所需输入。"""

    sql_logs: list[GenerationHistoryLog] = field(default_factory=list)
    chart_logs: list[GenerationHistoryLog] = field(default_factory=list)
    regenerate_record_id: int | None = None
    round_limit: int = 0


@dataclass(frozen=True, slots=True)
class GenerationHistoryProjectionResult:
    """可直接传入 SQL 与图表生成流程的历史消息。"""

    sql_history: list[ModelMessage] = field(default_factory=list)
    chart_history: list[ModelMessage] = field(default_factory=list)


__all__ = [
    "GenerationHistoryLog",
    "GenerationHistoryProjectionData",
    "GenerationHistoryProjectionResult",
]
