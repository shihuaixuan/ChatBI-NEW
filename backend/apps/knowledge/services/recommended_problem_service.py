from datetime import datetime

import orjson

from apps.knowledge.errors import RecommendedProblemRequestError
from apps.knowledge.models.dto import (
    RecommendedProblemBase,
    RecommendedProblemItem,
    RecommendedProblemResponse,
)
from apps.knowledge.repository import RecommendedProblemRepository


class RecommendedProblemService:
    """推荐问题维护与兼容查询的唯一业务入口。"""

    def __init__(self, repository: RecommendedProblemRepository) -> None:
        self._repository = repository

    def list_by_datasource(
        self,
        datasource_id: int,
    ) -> list[RecommendedProblemItem]:
        return self._repository.list_by_datasource(datasource_id)

    def get_base(self, datasource_id: int) -> RecommendedProblemResponse:
        recommended_config = self._repository.get_recommended_config(datasource_id)
        if recommended_config is None:
            return RecommendedProblemResponse(
                datasource_id=datasource_id,
                recommended_config=0,
            )
        if recommended_config == 1:
            return RecommendedProblemResponse(
                datasource_id=datasource_id,
                recommended_config=1,
            )

        questions = [
            item.question
            for item in self._repository.list_by_datasource(datasource_id)
        ]
        return RecommendedProblemResponse(
            datasource_id=datasource_id,
            recommended_config=recommended_config,
            questions=orjson.dumps(questions).decode(),
        )

    def list_for_chat(self, datasource_id: int) -> list[str] | None:
        """自定义模式返回问题；其他模式交由 ChatBI 自动生成。"""

        if self._repository.get_recommended_config(datasource_id) != 2:
            return None
        return [
            item.question
            for item in self._repository.list_by_datasource(datasource_id)
        ]

    def replace(
        self,
        request: RecommendedProblemBase,
        *,
        actor_id: int,
    ) -> None:
        if request.datasource_id is None:
            raise RecommendedProblemRequestError("datasource_id is required")
        if request.recommended_config is None:
            raise RecommendedProblemRequestError("recommended_config is required")

        now = datetime.now()
        problems = [
            item.model_copy(
                update={
                    "id": None,
                    "datasource_id": request.datasource_id,
                    "create_time": now,
                    "create_by": actor_id,
                }
            )
            for item in request.problemInfo
        ]
        self._repository.replace(
            request.datasource_id,
            request.recommended_config,
            problems,
        )
