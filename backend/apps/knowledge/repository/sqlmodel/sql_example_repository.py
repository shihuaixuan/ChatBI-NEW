from datetime import datetime

from sqlalchemy import delete, func, or_, text
from sqlmodel import Session, col, select

from apps.knowledge.errors import SQLExampleNotFoundError
from apps.knowledge.models.dto import (
    SQLExampleMatch,
    SQLExampleRecord,
    SQLExampleVerificationStatus,
)
from apps.knowledge.models.orm import SQLExampleModel


class SQLModelSQLExampleRepository:
    """SQL 示例源数据的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def count(self, workspace_id: int, keyword: str | None = None) -> int:
        statement = select(func.count()).select_from(SQLExampleModel).where(
            col(SQLExampleModel.oid) == workspace_id
        )
        normalized_keyword = self._normalized_keyword(keyword)
        if normalized_keyword is not None:
            statement = statement.where(
                col(SQLExampleModel.question).ilike(f"%{normalized_keyword}%")
            )
        return int(self._session.exec(statement).one())

    def list_by_workspace(
        self,
        workspace_id: int,
        keyword: str | None = None,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[SQLExampleRecord]:
        statement = select(SQLExampleModel).where(
            col(SQLExampleModel.oid) == workspace_id
        )
        normalized_keyword = self._normalized_keyword(keyword)
        if normalized_keyword is not None:
            statement = statement.where(
                col(SQLExampleModel.question).ilike(f"%{normalized_keyword}%")
            )
        statement = statement.order_by(
            col(SQLExampleModel.create_time).desc(),
            col(SQLExampleModel.id).desc(),
        ).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self._session.exec(statement).all()
        return [SQLExampleRecord.model_validate(row) for row in rows]

    def get(self, workspace_id: int, example_id: int) -> SQLExampleRecord | None:
        row = self._session.exec(
            select(SQLExampleModel).where(
                col(SQLExampleModel.id) == example_id,
                col(SQLExampleModel.oid) == workspace_id,
            )
        ).first()
        return SQLExampleRecord.model_validate(row) if row is not None else None

    def duplicate_exists(
        self,
        workspace_id: int,
        question: str,
        datasource_id: int | None,
        assistant_id: int | None,
        *,
        exclude_id: int | None = None,
    ) -> bool:
        statement = select(SQLExampleModel.id).where(
            col(SQLExampleModel.oid) == workspace_id,
            col(SQLExampleModel.question) == question,
        )
        if exclude_id is not None:
            statement = statement.where(col(SQLExampleModel.id) != exclude_id)
        if datasource_id is not None and assistant_id is not None:
            statement = statement.where(
                or_(
                    col(SQLExampleModel.datasource) == datasource_id,
                    col(SQLExampleModel.advanced_application) == assistant_id,
                )
            )
        elif datasource_id is not None:
            statement = statement.where(
                col(SQLExampleModel.datasource) == datasource_id
            )
        elif assistant_id is not None:
            statement = statement.where(
                col(SQLExampleModel.advanced_application) == assistant_id
            )
        return self._session.exec(statement).first() is not None

    def create(self, example: SQLExampleRecord) -> int:
        row = SQLExampleModel(**example.model_dump(exclude={"id"}))
        self._session.add(row)
        self._session.flush()
        self._session.refresh(row)
        if row.id is None:
            raise RuntimeError("Created SQL example has no ID")
        return row.id

    def update(self, example: SQLExampleRecord) -> int:
        if example.id is None:
            raise SQLExampleNotFoundError()
        row = self._session.exec(
            select(SQLExampleModel).where(
                col(SQLExampleModel.id) == example.id,
                col(SQLExampleModel.oid) == example.oid,
            )
        ).first()
        if row is None:
            raise SQLExampleNotFoundError()
        for field in (
            "datasource",
            "question",
            "description",
            "example_type",
            "sql",
            "linked_assets",
            "dataset_id",
            "enabled",
            "verification_status",
            "advanced_application",
            "source",
            "verified_by",
            "verified_at",
            "semantic_plan",
            "plan_fingerprint",
            "use_as_onboarding",
        ):
            setattr(row, field, getattr(example, field))
        self._session.add(row)
        self._session.flush()
        return example.id

    def set_verification_status(
        self,
        workspace_id: int,
        example_id: int,
        status: SQLExampleVerificationStatus,
        *,
        verified_by: int | None,
        verified_at: datetime | None,
    ) -> SQLExampleRecord | None:
        """verified query 生命周期流转（verify/deprecate），只改状态与审计列。"""

        row = self._session.exec(
            select(SQLExampleModel).where(
                col(SQLExampleModel.id) == example_id,
                col(SQLExampleModel.oid) == workspace_id,
            )
        ).first()
        if row is None:
            return None
        row.verification_status = status.value
        row.verified_by = verified_by
        row.verified_at = verified_at
        self._session.add(row)
        self._session.flush()
        return SQLExampleRecord.model_validate(row)

    def delete(self, workspace_id: int, example_ids: list[int]) -> None:
        if not example_ids:
            return
        self._session.exec(
            delete(SQLExampleModel).where(
                col(SQLExampleModel.oid) == workspace_id,
                col(SQLExampleModel.id).in_(example_ids),
            )
        )
        self._session.flush()

    def set_enabled(
        self,
        workspace_id: int,
        example_id: int,
        enabled: bool,
    ) -> bool:
        row = self._session.exec(
            select(SQLExampleModel).where(
                col(SQLExampleModel.id) == example_id,
                col(SQLExampleModel.oid) == workspace_id,
            )
        ).first()
        if row is None:
            return False
        row.enabled = enabled
        self._session.add(row)
        self._session.flush()
        return True

    def commit(self) -> None:
        """提交源数据与同 Session 暂存的 Retrieval durable job。"""

        self._session.commit()

    def search_lexical_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        statement = select(SQLExampleModel.id).where(
            text(
                "(:sentence ILIKE '%' || question || '%') "
                "OR (question ILIKE '%' || :sentence || '%')"
            ),
            col(SQLExampleModel.oid) == workspace_id,
            col(SQLExampleModel.enabled).is_(True),
            col(SQLExampleModel.verification_status)
            == SQLExampleVerificationStatus.VERIFIED.value,
        )
        if assistant_id is not None:
            statement = statement.where(
                col(SQLExampleModel.advanced_application) == assistant_id
            )
        else:
            statement = statement.where(
                col(SQLExampleModel.datasource) == datasource_id
            )
        rows = self._session.execute(
            statement,
            {"sentence": question},
        ).scalars().all()
        return [int(value) for value in rows]

    def get_matches(
        self,
        workspace_id: int,
        example_ids: list[int],
    ) -> list[SQLExampleMatch]:
        if not example_ids:
            return []
        rows = self._session.exec(
            select(SQLExampleModel).where(
                col(SQLExampleModel.id).in_(example_ids),
                col(SQLExampleModel.oid) == workspace_id,
                col(SQLExampleModel.enabled).is_(True),
                col(SQLExampleModel.verification_status)
                == SQLExampleVerificationStatus.VERIFIED.value,
            )
        ).all()
        by_id = {row.id: row for row in rows if row.id is not None}
        return [
            SQLExampleMatch(
                id=example_id,
                question=self._required_text(
                    by_id[example_id].question,
                    "question",
                ),
                suggestion_answer=self._required_text(
                    by_id[example_id].description,
                    "description",
                ),
            )
            for example_id in example_ids
            if example_id in by_id
        ]

    @staticmethod
    def _normalized_keyword(keyword: str | None) -> str | None:
        if keyword is None or not keyword.strip():
            return None
        return keyword.strip()

    @staticmethod
    def _required_text(value: str | None, field: str) -> str:
        if value is None:
            raise ValueError(f"SQL_EXAMPLE_{field.upper()}_MISSING")
        return value
