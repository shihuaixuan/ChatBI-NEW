"""XPack 行列权限持久化事实 SQLModel 适配。"""

from sqlalchemy import bindparam, text
from sqlmodel import Session

from apps.access_control.models.dto import StoredDataPermission, StoredDataRule


class SQLModelDataPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_permissions(
        self,
        datasource_id: int,
        table_ids: set[int],
    ) -> list[StoredDataPermission]:
        if not table_ids:
            return []
        # 这里只读取 Access Control 需要的持久化事实，避免依赖 XPack 具体 ORM。
        statement = text(
            "SELECT id, type, ds_id, table_id, expression_tree, permissions, "
            "white_list_user FROM ds_permission "
            "WHERE ds_id = :datasource_id "
            "AND table_id IN :table_ids "
            "AND enable IS TRUE "
            "AND type IN ('row', 'column')"
        ).bindparams(bindparam("table_ids", expanding=True))
        rows = self._session.execute(
            statement,
            {
                "datasource_id": datasource_id,
                "table_ids": sorted(table_ids),
            },
        ).mappings()
        return [
            StoredDataPermission(
                id=int(row["id"]),
                permission_type=str(row["type"]),
                datasource_id=int(row["ds_id"]),
                table_id=int(row["table_id"]),
                expression_tree=row["expression_tree"],
                permissions=row["permissions"],
                white_list_user=row["white_list_user"],
            )
            for row in rows
        ]

    def list_rules(self, workspace_id: int) -> list[StoredDataRule]:
        rows = self._session.execute(
            text(
                "SELECT id, oid, permission_list, user_list, white_list_user "
                "FROM ds_rules WHERE oid = :workspace_id AND enable IS TRUE"
            ),
            {"workspace_id": workspace_id},
        ).mappings()
        return [
            StoredDataRule(
                id=int(row["id"]),
                workspace_id=int(row["oid"]),
                permission_list=row["permission_list"],
                user_list=row["user_list"],
                white_list_user=row["white_list_user"],
            )
            for row in rows
        ]
