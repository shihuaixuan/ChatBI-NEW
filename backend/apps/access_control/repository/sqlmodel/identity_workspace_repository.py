"""用户、工作空间与成员关系的 SQLModel 仓储。"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete as sqlalchemy_delete
from sqlmodel import Session, col, exists, func, or_, select

from apps.access_control.models.dto import (
    UserCreator,
    UserEditor,
    UserGrid,
    UserRecord,
    UserWs,
    UserWsOption,
    WorkspaceRecord,
    WorkspaceUser,
)
from apps.access_control.models.orm import (
    UserModel,
    UserPlatformModel,
    UserWsModel,
    WorkspaceModel,
)
from common.core.pagination import Paginator
from common.core.schemas import PaginatedResponse, PaginationParams


class SQLModelIdentityWorkspaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _user_record(user: UserModel) -> UserRecord:
        return UserRecord.model_validate(user.model_dump())

    @staticmethod
    def _workspace_record(workspace: WorkspaceModel) -> WorkspaceRecord:
        return WorkspaceRecord.model_validate(workspace.model_dump())

    def get_user(self, user_id: int) -> UserRecord | None:
        user = self._session.get(UserModel, user_id)
        return self._user_record(user) if user else None

    def get_user_by_account(self, account: str) -> UserRecord | None:
        user = self._session.exec(
            select(UserModel).where(col(UserModel.account) == account)
        ).first()
        return self._user_record(user) if user else None

    def account_exists(self, account: str) -> bool:
        return (
            self._session.exec(
                select(func.count())
                .select_from(UserModel)
                .where(col(UserModel.account) == account)
            ).one()
            > 0
        )

    def email_exists(self, email: str) -> bool:
        return (
            self._session.exec(
                select(func.count())
                .select_from(UserModel)
                .where(col(UserModel.email) == email)
            ).one()
            > 0
        )

    def find_missing_user_ids(self, user_ids: set[int]) -> set[int]:
        if not user_ids:
            return set()
        existing = set(
            self._session.exec(
                select(UserModel.id).where(col(UserModel.id).in_(user_ids))
            ).all()
        )
        return user_ids - existing

    def find_missing_workspace_ids(self, workspace_ids: set[int]) -> set[int]:
        if not workspace_ids:
            return set()
        existing = set(
            self._session.exec(
                select(WorkspaceModel.id).where(
                    col(WorkspaceModel.id).in_(workspace_ids)
                )
            ).all()
        )
        return workspace_ids - existing

    def get_membership_weight(self, user_id: int, workspace_id: int) -> int | None:
        return self._session.exec(
            select(UserWsModel.weight).where(
                col(UserWsModel.uid) == user_id,
                col(UserWsModel.oid) == workspace_id,
            )
        ).first()

    def list_user_workspaces(self, user_id: int) -> list[UserWs]:
        if user_id == 1:
            statement = select(WorkspaceModel.id, WorkspaceModel.name).order_by(
                col(WorkspaceModel.name),
                col(WorkspaceModel.create_time),
            )
        else:
            statement = (
                select(WorkspaceModel.id, WorkspaceModel.name)
                .join(UserWsModel, col(UserWsModel.oid) == col(WorkspaceModel.id))
                .where(col(UserWsModel.uid) == user_id)
                .order_by(col(WorkspaceModel.name), col(WorkspaceModel.create_time))
            )
        return [
            UserWs(id=int(workspace_id), name=name)
            for workspace_id, name in self._session.exec(statement).all()
            if workspace_id is not None
        ]

    async def list_users(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
        status: int | None,
        origins: list[int] | None,
        workspace_ids: list[int] | None,
    ) -> PaginatedResponse[UserGrid]:
        statement = (
            select(UserModel.id, UserModel.account)
            .join(UserWsModel, col(UserModel.id) == col(UserWsModel.uid), isouter=True)
            .where(col(UserModel.id) != 1)
            .distinct()
            .order_by(col(UserModel.account))
        )
        if workspace_ids:
            statement = statement.where(col(UserWsModel.oid).in_(workspace_ids))
        if origins:
            statement = statement.where(col(UserModel.origin).in_(origins))
        if status is not None:
            statement = statement.where(col(UserModel.status) == status)
        if keyword:
            keyword_pattern = f"%{keyword}%"
            statement = statement.where(
                or_(
                    col(UserModel.account).ilike(keyword_pattern),
                    col(UserModel.name).ilike(keyword_pattern),
                    col(UserModel.email).ilike(keyword_pattern),
                )
            )

        page_result = await Paginator(self._session).get_paginated_response(
            statement,
            PaginationParams(page=page, size=size),
        )
        user_ids = [int(item["id"]) for item in page_result.items]
        if not user_ids:
            return PaginatedResponse[UserGrid](
                items=[],
                total=page_result.total,
                page=page_result.page,
                size=page_result.size,
                total_pages=page_result.total_pages,
            )

        rows: Sequence[Any] = self._session.exec(
            select(UserModel, col(UserWsModel.oid).label("ws_oid"))
            .join(UserWsModel, col(UserModel.id) == col(UserWsModel.uid), isouter=True)
            .where(col(UserModel.id).in_(user_ids))
            .order_by(col(UserModel.account), col(UserModel.create_time))
        ).all()
        workspace_ids_by_user: dict[int, list[int]] = defaultdict(list)
        users_by_id: dict[int, dict[str, Any]] = {}
        for user, workspace_id in rows:
            if user.id is None:
                continue
            users_by_id[user.id] = user.model_dump()
            if workspace_id is not None:
                workspace_ids_by_user[user.id].append(workspace_id)

        items = [
            UserGrid.model_validate(
                {
                    **users_by_id[user_id],
                    "oid_list": workspace_ids_by_user[user_id],
                }
            )
            for user_id in user_ids
        ]
        return PaginatedResponse[UserGrid](
            items=items,
            total=page_result.total,
            page=page_result.page,
            size=page_result.size,
            total_pages=page_result.total_pages,
        )

    def create_user(
        self,
        creator: UserCreator,
        workspace_ids: list[int],
    ) -> UserRecord:
        data = creator.model_dump(exclude={"oid", "oid_list"})
        user = UserModel.model_validate(data)
        user.language = "zh-CN"
        user.oid = workspace_ids[0] if workspace_ids else 0
        self._session.add(user)
        self._session.flush()
        if user.id is None:
            raise RuntimeError("ACCESS_CONTROL_USER_ID_NOT_GENERATED")
        self._session.add_all(
            [UserWsModel(uid=user.id, oid=workspace_id, weight=0) for workspace_id in workspace_ids]
        )
        self._session.commit()
        self._session.refresh(user)
        return self._user_record(user)

    def update_user(
        self,
        editor: UserEditor,
        workspace_ids: list[int],
    ) -> UserRecord | None:
        user = self._session.get(UserModel, editor.id)
        if user is None:
            return None
        existing_memberships = self._session.exec(
            select(UserWsModel).where(col(UserWsModel.uid) == editor.id)
        ).all()
        existing_workspace_ids = {membership.oid for membership in existing_memberships}
        new_workspace_ids = set(workspace_ids)
        removed_workspace_ids = existing_workspace_ids - new_workspace_ids
        added_workspace_ids = new_workspace_ids - existing_workspace_ids

        if removed_workspace_ids:
            self._session.exec(
                sqlalchemy_delete(UserWsModel).where(
                    col(UserWsModel.uid) == editor.id,
                    col(UserWsModel.oid).in_(removed_workspace_ids),
                )
            )
        if added_workspace_ids:
            self._session.add_all(
                [
                    UserWsModel(uid=editor.id, oid=workspace_id, weight=0)
                    for workspace_id in added_workspace_ids
                ]
            )

        user.sqlmodel_update(
            editor.model_dump(exclude_unset=True, exclude={"id", "oid", "oid_list"})
        )
        if user.oid not in new_workspace_ids:
            user.oid = workspace_ids[0] if workspace_ids else 0
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return self._user_record(user)

    def delete_users(self, user_ids: list[int]) -> list[int]:
        users = self._session.exec(
            select(UserModel).where(col(UserModel.id).in_(user_ids))
        ).all()
        found_ids = [user.id for user in users if user.id is not None]
        if not found_ids:
            return []
        self._session.exec(
            sqlalchemy_delete(UserWsModel).where(col(UserWsModel.uid).in_(found_ids))
        )
        self._session.exec(
            sqlalchemy_delete(UserPlatformModel).where(
                col(UserPlatformModel.uid).in_(found_ids)
            )
        )
        for user in users:
            self._session.delete(user)
        self._session.commit()
        return found_ids

    def set_current_workspace(self, user_id: int, workspace_id: int) -> None:
        user = self._session.get(UserModel, user_id)
        if user is None:
            return
        user.oid = workspace_id
        self._session.add(user)
        self._session.commit()

    def update_user_language(self, user_id: int, language: str) -> UserRecord | None:
        return self._update_user_field(user_id, "language", language)

    def update_user_password(self, user_id: int, password: str) -> UserRecord | None:
        return self._update_user_field(user_id, "password", password)

    def update_user_status(self, user_id: int, status: int) -> UserRecord | None:
        return self._update_user_field(user_id, "status", status)

    def _update_user_field(
        self,
        user_id: int,
        field_name: str,
        value: object,
    ) -> UserRecord | None:
        user = self._session.get(UserModel, user_id)
        if user is None:
            return None
        setattr(user, field_name, value)
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return self._user_record(user)

    def get_workspace(self, workspace_id: int) -> WorkspaceRecord | None:
        workspace = self._session.get(WorkspaceModel, workspace_id)
        return self._workspace_record(workspace) if workspace else None

    def list_workspaces(self) -> list[WorkspaceRecord]:
        return [
            self._workspace_record(workspace)
            for workspace in self._session.exec(select(WorkspaceModel)).all()
        ]

    def create_workspace(self, name: str, create_time: int) -> WorkspaceRecord:
        workspace = WorkspaceModel(name=name, create_time=create_time)
        self._session.add(workspace)
        self._session.commit()
        self._session.refresh(workspace)
        return self._workspace_record(workspace)

    def update_workspace(
        self,
        workspace_id: int,
        name: str,
    ) -> WorkspaceRecord | None:
        workspace = self._session.get(WorkspaceModel, workspace_id)
        if workspace is None:
            return None
        workspace.name = name
        self._session.add(workspace)
        self._session.commit()
        self._session.refresh(workspace)
        return self._workspace_record(workspace)

    def delete_workspace(
        self,
        workspace_id: int,
        default_workspace_id: int,
    ) -> list[int] | None:
        workspace = self._session.get(WorkspaceModel, workspace_id)
        if workspace is None:
            return None

        affected_users = self._session.exec(
            select(UserModel).where(col(UserModel.oid) == workspace_id)
        ).all()
        affected_user_ids = [user.id for user in affected_users if user.id is not None]
        alternate_rows = self._session.exec(
            select(UserWsModel.uid, UserWsModel.oid)
            .where(
                col(UserWsModel.uid).in_(affected_user_ids),
                col(UserWsModel.oid) != workspace_id,
            )
            .order_by(col(UserWsModel.id))
        ).all()
        alternate_by_user: dict[int, int] = {}
        for user_id, alternate_workspace_id in alternate_rows:
            alternate_by_user.setdefault(user_id, alternate_workspace_id)

        for user in affected_users:
            if user.id is None:
                continue
            user.oid = alternate_by_user.get(
                user.id,
                default_workspace_id if user.id == 1 else 0,
            )
            self._session.add(user)

        member_user_ids = set(
            self._session.exec(
                select(UserWsModel.uid).where(col(UserWsModel.oid) == workspace_id)
            ).all()
        )
        self._session.exec(
            sqlalchemy_delete(UserWsModel).where(col(UserWsModel.oid) == workspace_id)
        )
        self._session.delete(workspace)
        self._session.commit()
        return sorted(member_user_ids | set(affected_user_ids))

    async def list_available_users(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[UserWsOption]:
        statement = (
            select(UserModel.id, UserModel.account, UserModel.name)
            .where(
                ~exists().where(
                    col(UserWsModel.uid) == col(UserModel.id),
                    col(UserWsModel.oid) == workspace_id,
                ),
                col(UserModel.id) != 1,
            )
            .order_by(col(UserModel.account), col(UserModel.create_time))
        )
        if keyword:
            keyword_pattern = f"%{keyword}%"
            statement = statement.where(
                or_(
                    col(UserModel.account).ilike(keyword_pattern),
                    col(UserModel.name).ilike(keyword_pattern),
                )
            )
        result = await Paginator(self._session).get_paginated_response(
            statement,
            PaginationParams(page=page, size=size),
        )
        return PaginatedResponse[UserWsOption](
            items=[UserWsOption.model_validate(item) for item in result.items],
            total=result.total,
            page=result.page,
            size=result.size,
            total_pages=result.total_pages,
        )

    def find_available_user(
        self,
        *,
        workspace_id: int,
        keyword: str,
    ) -> UserWsOption | None:
        row = self._session.exec(
            select(UserModel.id, UserModel.account, UserModel.name).where(
                ~exists().where(
                    col(UserWsModel.uid) == col(UserModel.id),
                    col(UserWsModel.oid) == workspace_id,
                ),
                col(UserModel.id) != 1,
                or_(
                    col(UserModel.account) == keyword,
                    col(UserModel.name) == keyword,
                ),
            )
        ).first()
        return (
            UserWsOption(id=int(row[0]), account=row[1], name=row[2])
            if row and row[0] is not None
            else None
        )

    async def list_workspace_members(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[WorkspaceUser]:
        statement = (
            select(  # type: ignore[call-overload]
                col(UserModel.id),
                col(UserModel.account),
                col(UserModel.name),
                col(UserModel.email),
                col(UserModel.status),
                col(UserModel.create_time),
                col(UserModel.oid),
                col(UserWsModel.weight),
            )
            .join(UserWsModel, col(UserModel.id) == col(UserWsModel.uid))
            .where(
                col(UserWsModel.oid) == workspace_id,
                col(UserModel.id) != 1,
            )
            .order_by(col(UserModel.account), col(UserModel.create_time))
        )
        if keyword:
            keyword_pattern = f"%{keyword}%"
            statement = statement.where(
                or_(
                    col(UserModel.account).ilike(keyword_pattern),
                    col(UserModel.name).ilike(keyword_pattern),
                    col(UserModel.email).ilike(keyword_pattern),
                )
            )
        result = await Paginator(self._session).get_paginated_response(
            statement,
            PaginationParams(page=page, size=size),
        )
        return PaginatedResponse[WorkspaceUser](
            items=[WorkspaceUser.model_validate(item) for item in result.items],
            total=result.total,
            page=result.page,
            size=result.size,
            total_pages=result.total_pages,
        )

    def find_existing_member_ids(
        self,
        workspace_id: int,
        user_ids: set[int],
    ) -> set[int]:
        if not user_ids:
            return set()
        return set(
            self._session.exec(
                select(UserWsModel.uid).where(
                    col(UserWsModel.oid) == workspace_id,
                    col(UserWsModel.uid).in_(user_ids),
                )
            ).all()
        )

    def bind_members(
        self,
        workspace_id: int,
        user_ids: list[int],
        weight: int,
    ) -> None:
        users = self._session.exec(
            select(UserModel).where(col(UserModel.id).in_(user_ids))
        ).all()
        self._session.add_all(
            [
                UserWsModel(uid=user_id, oid=workspace_id, weight=weight)
                for user_id in user_ids
            ]
        )
        for user in users:
            if user.oid == 0:
                user.oid = workspace_id
                self._session.add(user)
        self._session.commit()

    def update_member_weight(
        self,
        workspace_id: int,
        user_id: int,
        weight: int,
    ) -> bool:
        membership = self._session.exec(
            select(UserWsModel).where(
                col(UserWsModel.uid) == user_id,
                col(UserWsModel.oid) == workspace_id,
            )
        ).first()
        if membership is None:
            return False
        if membership.weight != weight:
            membership.weight = weight
            self._session.add(membership)
            self._session.commit()
        return True

    def unbind_members(
        self,
        workspace_id: int,
        user_ids: list[int],
    ) -> list[int]:
        memberships = self._session.exec(
            select(UserWsModel).where(
                col(UserWsModel.uid).in_(user_ids),
                col(UserWsModel.oid) == workspace_id,
            )
        ).all()
        if not memberships:
            return []
        found_user_ids = [membership.uid for membership in memberships]
        users = self._session.exec(
            select(UserModel).where(col(UserModel.id).in_(found_user_ids))
        ).all()
        alternate_rows = self._session.exec(
            select(UserWsModel.uid, UserWsModel.oid)
            .where(
                col(UserWsModel.uid).in_(found_user_ids),
                col(UserWsModel.oid) != workspace_id,
            )
            .order_by(col(UserWsModel.id))
        ).all()
        alternate_by_user: dict[int, int] = {}
        for user_id, alternate_workspace_id in alternate_rows:
            alternate_by_user.setdefault(user_id, alternate_workspace_id)
        for user in users:
            if user.id is not None and user.oid == workspace_id:
                user.oid = alternate_by_user.get(user.id, 0)
                self._session.add(user)
        for membership in memberships:
            self._session.delete(membership)
        self._session.commit()
        return found_user_ids
