from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class QueryResultProjectionData:
    """旧 Chat 查询结果标准化和记录投影所需的稳定输入。"""

    record_id: int
    datasource_id: int
    fields: list[str]
    rows: list[dict[str, Any]]
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    enable_row_limit: bool = True
    row_limit: int = 1000


__all__ = ["QueryResultProjectionData"]
