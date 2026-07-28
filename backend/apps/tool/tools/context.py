"""公共 Tool 可读取的最小可信上下文。"""

from __future__ import annotations

from typing import Protocol


class TrustedToolContext(Protocol):
    """由宿主确认身份和范围，公共 Tool 不读取宿主可变状态。"""

    @property
    def workspace_id(self) -> int: ...

    @property
    def user_id(self) -> int | None: ...

    @property
    def datasource_id(self) -> int | None: ...

    @property
    def dataset_id(self) -> int | None: ...

    @property
    def selected_tables(self) -> list[str]: ...

    @property
    def summary_max_chars(self) -> int: ...


__all__ = ["TrustedToolContext"]
