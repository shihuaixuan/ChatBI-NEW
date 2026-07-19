import orjson
import pytest

from apps.knowledge.errors import RecommendedProblemRequestError
from apps.knowledge.models.dto import (
    RecommendedProblemBase,
    RecommendedProblemItem,
)
from apps.knowledge.services import RecommendedProblemService


class RecordingRecommendedProblemRepository:
    def __init__(self) -> None:
        self.configs: dict[int, int] = {}
        self.problems: dict[int, list[RecommendedProblemItem]] = {}
        self.replaced: tuple[int, int, list[RecommendedProblemItem]] | None = None

    def list_by_datasource(
        self,
        datasource_id: int,
    ) -> list[RecommendedProblemItem]:
        return self.problems.get(datasource_id, [])

    def get_recommended_config(self, datasource_id: int) -> int | None:
        return self.configs.get(datasource_id)

    def replace(
        self,
        datasource_id: int,
        recommended_config: int,
        problems: list[RecommendedProblemItem],
    ) -> None:
        self.replaced = (datasource_id, recommended_config, problems)


def test_base_response_keeps_existing_json_string_contract():
    repository = RecordingRecommendedProblemRepository()
    repository.configs[8] = 2
    repository.problems[8] = [
        RecommendedProblemItem(question="第二问", sort=2),
        RecommendedProblemItem(question="第一问", sort=1),
    ]
    service = RecommendedProblemService(repository)

    response = service.get_base(8)

    assert response.datasource_id == 8
    assert response.recommended_config == 2
    assert orjson.loads(response.questions) == ["第二问", "第一问"]


@pytest.mark.parametrize(
    ("config", "expected_config"),
    [(None, 0), (1, 1)],
)
def test_base_response_omits_questions_outside_custom_mode(
    config: int | None,
    expected_config: int,
):
    repository = RecordingRecommendedProblemRepository()
    if config is not None:
        repository.configs[8] = config
    service = RecommendedProblemService(repository)

    response = service.get_base(8)

    assert response.recommended_config == expected_config
    assert response.questions is None


def test_replace_uses_request_datasource_and_current_actor_for_every_item():
    repository = RecordingRecommendedProblemRepository()
    service = RecommendedProblemService(repository)
    request = RecommendedProblemBase(
        datasource_id=8,
        recommended_config=2,
        problemInfo=[
            RecommendedProblemItem(
                id=99,
                datasource_id=777,
                question="销售趋势如何？",
                create_by=100,
            )
        ],
    )

    service.replace(request, actor_id=7)

    assert repository.replaced is not None
    datasource_id, config, problems = repository.replaced
    assert datasource_id == 8
    assert config == 2
    assert len(problems) == 1
    assert problems[0].id is None
    assert problems[0].datasource_id == 8
    assert problems[0].create_by == 7
    assert problems[0].create_time is not None


def test_replace_requires_datasource_and_config():
    service = RecommendedProblemService(RecordingRecommendedProblemRepository())

    with pytest.raises(RecommendedProblemRequestError, match="datasource_id"):
        service.replace(RecommendedProblemBase(), actor_id=7)

    with pytest.raises(RecommendedProblemRequestError, match="recommended_config"):
        service.replace(
            RecommendedProblemBase(datasource_id=8),
            actor_id=7,
        )


def test_chat_questions_are_only_returned_for_custom_mode():
    repository = RecordingRecommendedProblemRepository()
    repository.problems[8] = [RecommendedProblemItem(question="销售趋势如何？")]
    service = RecommendedProblemService(repository)

    repository.configs[8] = 1
    assert service.list_for_chat(8) is None

    repository.configs[8] = 2
    assert service.list_for_chat(8) == ["销售趋势如何？"]
