"""权限变量 SQLModel 仓储。"""

from sqlalchemy import and_
from sqlalchemy import delete as sqlalchemy_delete
from sqlmodel import Session, col, select

from apps.access_control.models.dto import (
    AccessVariableCreateData,
    AccessVariableRecord,
    AccessVariableUpdateData,
)
from apps.access_control.models.orm import AccessVariableModel
from common.core.pagination import Paginator
from common.core.schemas import PaginatedResponse, PaginationParams


class SQLModelAccessVariableRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(model: AccessVariableModel) -> AccessVariableRecord:
        return AccessVariableRecord.model_validate(model.model_dump())

    def get(self, variable_id: int) -> AccessVariableRecord | None:
        model = self._session.get(AccessVariableModel, variable_id)
        return self._record(model) if model else None

    def get_by_name(
        self,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> AccessVariableRecord | None:
        statement = select(AccessVariableModel).where(
            col(AccessVariableModel.name) == name
        )
        if exclude_id is not None:
            statement = statement.where(col(AccessVariableModel.id) != exclude_id)
        model = self._session.exec(statement).first()
        return self._record(model) if model else None

    def list_by_ids(self, variable_ids: set[int]) -> list[AccessVariableRecord]:
        if not variable_ids:
            return []
        models = self._session.exec(
            select(AccessVariableModel).where(
                col(AccessVariableModel.id).in_(variable_ids)
            )
        ).all()
        return [self._record(model) for model in models]

    def list_all(self, keyword: str | None = None) -> list[AccessVariableRecord]:
        statement = select(AccessVariableModel)
        if keyword:
            statement = statement.where(
                and_(
                    col(AccessVariableModel.name).ilike(f"%{keyword}%"),
                    col(AccessVariableModel.type) != "system",
                )
            )
        models = self._session.exec(
            statement.order_by(
                col(AccessVariableModel.type).desc(),
                col(AccessVariableModel.name),
            )
        ).all()
        return [self._record(model) for model in models]

    async def list_page(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[AccessVariableRecord]:
        statement = select(AccessVariableModel)
        if keyword:
            statement = statement.where(
                and_(
                    col(AccessVariableModel.name).ilike(f"%{keyword}%"),
                    col(AccessVariableModel.type) != "system",
                )
            )
        statement = statement.order_by(
            col(AccessVariableModel.type).desc(),
            col(AccessVariableModel.name),
        )
        page_result = await Paginator(self._session).get_paginated_response(
            stmt=statement,
            pagination=PaginationParams(page=page, size=size),
        )
        return PaginatedResponse[AccessVariableRecord](
            items=[
                self._record(item)
                if isinstance(item, AccessVariableModel)
                else AccessVariableRecord.model_validate(item)
                for item in page_result.items
            ],
            page=page_result.page,
            size=page_result.size,
            total=page_result.total,
            total_pages=page_result.total_pages,
        )

    def create(self, data: AccessVariableCreateData) -> AccessVariableRecord:
        model = AccessVariableModel.model_validate(data)
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._record(model)

    def update(
        self,
        variable_id: int,
        data: AccessVariableUpdateData,
    ) -> AccessVariableRecord | None:
        model = self._session.get(AccessVariableModel, variable_id)
        if model is None:
            return None
        model.sqlmodel_update(data)
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._record(model)

    def delete(self, variable_ids: list[int]) -> list[int]:
        if not variable_ids:
            return []
        self._session.exec(
            sqlalchemy_delete(AccessVariableModel).where(
                col(AccessVariableModel.id).in_(variable_ids)
            )
        )
        self._session.commit()
        return variable_ids
