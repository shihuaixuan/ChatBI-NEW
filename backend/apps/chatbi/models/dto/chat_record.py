from dataclasses import dataclass
from enum import Enum


class ChatRecordStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatRecordExecutionType(str, Enum):
    GRAPH = "graph"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class ChatRecordCreateData:
    chat_id: int
    user_id: int
    question: str
    dataset_id: int | None
    datasource_id: int | None
    engine_type: str
    execution_type: ChatRecordExecutionType
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRecordResultProjection:
    answer: str | None = None
    chart_answer: str | None = None
    sql: str | None = None
    chart: str | None = None
    data: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRecordResultLimits:
    """会话最终快照的持久化大小边界。"""

    max_answer_chars: int = 100_000
    max_sql_chars: int = 200_000
    max_chart_chars: int = 200_000
    max_data_bytes: int = 1_000_000


__all__ = [
    "ChatRecordCreateData",
    "ChatRecordExecutionType",
    "ChatRecordResultProjection",
    "ChatRecordResultLimits",
    "ChatRecordStatus",
]
