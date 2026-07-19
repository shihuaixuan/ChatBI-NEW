from typing import Protocol

from apps.knowledge.models.dto import RecommendedProblemItem


class RecommendedProblemRepository(Protocol):
    """推荐问题查询和整批替换的仓储端口。"""

    def list_by_datasource(
        self,
        datasource_id: int,
    ) -> list[RecommendedProblemItem]: ...

    def get_recommended_config(self, datasource_id: int) -> int | None: ...

    def replace(
        self,
        datasource_id: int,
        recommended_config: int,
        problems: list[RecommendedProblemItem],
    ) -> None: ...
