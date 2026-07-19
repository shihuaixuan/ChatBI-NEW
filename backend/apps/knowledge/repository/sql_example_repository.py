from typing import Protocol

from apps.knowledge.models.dto import (
    SQLExampleIndexEnqueueResult,
    SQLExampleMatch,
    SQLExampleRecord,
    SQLExampleSourceSnapshot,
)


class SQLExampleRepository(Protocol):
    """SQL 示例源数据仓储端口。"""

    def count(self, workspace_id: int, keyword: str | None = None) -> int: ...

    def list_by_workspace(
        self,
        workspace_id: int,
        keyword: str | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[SQLExampleRecord]: ...

    def get(self, workspace_id: int, example_id: int) -> SQLExampleRecord | None: ...

    def duplicate_exists(
        self,
        workspace_id: int,
        question: str,
        datasource_id: int | None,
        assistant_id: int | None,
        *,
        exclude_id: int | None = None,
    ) -> bool: ...

    def create(self, example: SQLExampleRecord) -> int: ...

    def update(self, example: SQLExampleRecord) -> int: ...

    def delete(self, workspace_id: int, example_ids: list[int]) -> None: ...

    def set_enabled(
        self,
        workspace_id: int,
        example_id: int,
        enabled: bool,
    ) -> bool: ...

    def commit(self) -> None: ...

    def search_lexical_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]: ...

    def get_matches(self, example_ids: list[int]) -> list[SQLExampleMatch]: ...


class SQLExampleReferenceCatalog(Protocol):
    """SQL 示例展示和导入使用的跨领域名称目录。"""

    def datasource_names(
        self,
        workspace_id: int,
        datasource_ids: list[int] | None = None,
    ) -> dict[int, str]: ...

    def assistant_names(
        self,
        workspace_id: int,
        assistant_ids: list[int] | None = None,
    ) -> dict[int, str]: ...


class SQLExampleRetrievalSearch(Protocol):
    """SQL 示例统一检索召回端口。"""

    def search_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]: ...


class SQLExampleIndexGateway(Protocol):
    """SQL 示例公开快照到 Retrieval durable generation 的提交端口。"""

    def stage_rebuild(
        self,
        snapshot: SQLExampleSourceSnapshot,
    ) -> SQLExampleIndexEnqueueResult: ...

    def submit(self, job_ids: tuple[int, ...]) -> None: ...
