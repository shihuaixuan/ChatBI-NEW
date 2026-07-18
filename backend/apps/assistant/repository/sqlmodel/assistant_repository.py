"""Assistant SQLModel 仓储。"""

from sqlmodel import Session, col, select

from apps.assistant.models.dto import (
    AssistantCreateData,
    AssistantRecord,
    AssistantReference,
    AssistantUpdateData,
)
from apps.assistant.models.orm import AssistantModel


class SQLModelAssistantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(model: AssistantModel) -> AssistantRecord:
        return AssistantRecord.model_validate(model.model_dump())

    def get(self, assistant_id: int) -> AssistantRecord | None:
        model = self._session.get(AssistantModel, assistant_id)
        return self._record(model) if model else None

    def get_by_app_id(self, app_id: str) -> AssistantRecord | None:
        model = self._session.exec(
            select(AssistantModel).where(col(AssistantModel.app_id) == app_id)
        ).first()
        return self._record(model) if model else None

    def list_for_workspace(
        self,
        workspace_id: int,
        *,
        assistant_type: int | None = None,
        exclude_type: int | None = None,
    ) -> list[AssistantRecord]:
        statement = select(AssistantModel).where(
            col(AssistantModel.oid) == workspace_id
        )
        if assistant_type is not None:
            statement = statement.where(col(AssistantModel.type) == assistant_type)
        if exclude_type is not None:
            statement = statement.where(col(AssistantModel.type) != exclude_type)
        models = self._session.exec(
            statement.order_by(
                col(AssistantModel.name),
                col(AssistantModel.create_time),
            )
        ).all()
        return [self._record(model) for model in models]

    def list_references(
        self,
        assistant_ids: list[int] | None = None,
        *,
        workspace_id: int | None = None,
        assistant_type: int | None = None,
    ) -> list[AssistantReference]:
        statement = select(AssistantModel)
        if assistant_ids is not None:
            if not assistant_ids:
                return []
            statement = statement.where(col(AssistantModel.id).in_(assistant_ids))
        if workspace_id is not None:
            statement = statement.where(col(AssistantModel.oid) == workspace_id)
        if assistant_type is not None:
            statement = statement.where(col(AssistantModel.type) == assistant_type)
        rows = self._session.exec(statement.order_by(col(AssistantModel.name))).all()
        return [
            AssistantReference(id=int(row.id), name=row.name)
            for row in rows
            if row.id is not None
        ]

    def list_domains(self) -> list[str]:
        return list(
            self._session.exec(
                select(AssistantModel.domain).order_by(col(AssistantModel.create_time))
            ).all()
        )

    def create(self, data: AssistantCreateData) -> AssistantRecord:
        model = AssistantModel.model_validate(data)
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._record(model)

    def update(
        self,
        assistant_id: int,
        data: AssistantUpdateData,
    ) -> AssistantRecord | None:
        model = self._session.get(AssistantModel, assistant_id)
        if model is None:
            return None
        model.sqlmodel_update(data)
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._record(model)

    def update_configuration(
        self,
        assistant_id: int,
        configuration: str,
    ) -> AssistantRecord | None:
        model = self._session.get(AssistantModel, assistant_id)
        if model is None:
            return None
        model.configuration = configuration
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._record(model)

    def delete(self, assistant_id: int) -> AssistantRecord | None:
        model = self._session.get(AssistantModel, assistant_id)
        if model is None:
            return None
        record = self._record(model)
        self._session.delete(model)
        self._session.commit()
        return record
