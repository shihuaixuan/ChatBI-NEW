import pytest

from apps.knowledge.errors import RecommendedProblemDatasourceNotFoundError
from apps.knowledge.models.dto import RecommendedProblemItem
from apps.knowledge.models.orm import RecommendedProblem
from apps.knowledge.repository.sqlmodel import SQLModelRecommendedProblemRepository


class RecordingDatasourceConfigStore:
    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists
        self.configs: dict[int, int] = {8: 1} if exists else {}
        self.staged: tuple[int, int] | None = None

    def get_recommended_config(self, datasource_id: int) -> int | None:
        return self.configs.get(datasource_id)

    def stage_recommended_config(
        self,
        datasource_id: int,
        recommended_config: int,
    ) -> bool:
        if not self.exists:
            return False
        self.staged = (datasource_id, recommended_config)
        self.configs[datasource_id] = recommended_config
        return True


class RecordingSession:
    def __init__(self, *, commit_failure: Exception | None = None) -> None:
        self.commit_failure = commit_failure
        self.statements: list[object] = []
        self.added: list[RecommendedProblem] = []
        self.commit_called = False
        self.rollback_called = False

    def exec(self, statement):
        self.statements.append(statement)
        return None

    def add(self, row: RecommendedProblem) -> None:
        self.added.append(row)

    def commit(self) -> None:
        self.commit_called = True
        if self.commit_failure is not None:
            raise self.commit_failure

    def rollback(self) -> None:
        self.rollback_called = True


class ListResult:
    def __init__(self, rows: list[RecommendedProblem]) -> None:
        self._rows = rows

    def all(self) -> list[RecommendedProblem]:
        return self._rows


class ListSession(RecordingSession):
    def __init__(self, rows: list[RecommendedProblem]) -> None:
        super().__init__()
        self._rows = rows

    def exec(self, statement):
        self.statements.append(statement)
        return ListResult(self._rows)


def test_list_query_orders_by_sort_then_id():
    rows = [
        RecommendedProblem(
            id=2,
            datasource_id=8,
            question="第一问",
            remark="",
            sort=1,
            create_by=7,
        )
    ]
    session = ListSession(rows)
    repository = SQLModelRecommendedProblemRepository(
        session,
        RecordingDatasourceConfigStore(),
    )

    result = repository.list_by_datasource(8)

    assert [item.question for item in result] == ["第一问"]
    statement = str(session.statements[0])
    assert "ORDER BY ds_recommended_problem.sort, ds_recommended_problem.id" in statement


def test_replace_stages_config_and_replaces_all_questions_before_one_commit():
    session = RecordingSession()
    config_store = RecordingDatasourceConfigStore()
    repository = SQLModelRecommendedProblemRepository(session, config_store)
    problems = [
        RecommendedProblemItem(
            datasource_id=8,
            question="销售趋势如何？",
            sort=1,
            create_by=7,
        )
    ]

    repository.replace(8, 2, problems)

    assert config_store.staged == (8, 2)
    assert len(session.statements) == 1
    assert str(session.statements[0]).startswith("DELETE FROM ds_recommended_problem")
    assert [row.question for row in session.added] == ["销售趋势如何？"]
    assert session.commit_called
    assert not session.rollback_called


def test_replace_rolls_back_config_and_questions_when_commit_fails():
    session = RecordingSession(commit_failure=RuntimeError("提交失败"))
    repository = SQLModelRecommendedProblemRepository(
        session,
        RecordingDatasourceConfigStore(),
    )

    with pytest.raises(RuntimeError, match="提交失败"):
        repository.replace(
            8,
            2,
            [RecommendedProblemItem(datasource_id=8, question="销售趋势如何？")],
        )

    assert session.rollback_called


def test_replace_rejects_missing_datasource_before_deleting_questions():
    session = RecordingSession()
    repository = SQLModelRecommendedProblemRepository(
        session,
        RecordingDatasourceConfigStore(exists=False),
    )

    with pytest.raises(RecommendedProblemDatasourceNotFoundError):
        repository.replace(99, 2, [])

    assert session.statements == []
    assert session.rollback_called
    assert not session.commit_called
