import pytest

from apps.knowledge.models.dto import SQLExampleRecord
from apps.knowledge.repository.sqlmodel import SQLModelSQLExampleRepository


class FailingCommitSession:
    def __init__(self) -> None:
        self.rollback_called = False
        self.added = None

    def add(self, row) -> None:
        self.added = row

    def flush(self) -> None:
        assert self.added is not None
        self.added.id = 10

    def refresh(self, _row) -> None:
        return None

    def commit(self) -> None:
        raise RuntimeError("提交失败")

    def rollback(self) -> None:
        self.rollback_called = True


def test_repository_rolls_back_when_create_commit_fails():
    session = FailingCommitSession()
    repository = SQLModelSQLExampleRepository(session)

    with pytest.raises(RuntimeError, match="提交失败"):
        repository.create(
            SQLExampleRecord(
                oid=3,
                datasource=8,
                question="本月销售额",
                description="SELECT 1",
            )
        )

    assert session.rollback_called
