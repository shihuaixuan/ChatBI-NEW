"""数据行列权限配置接口。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlmodel import col, select

from apps.access_control.models.orm import DataPermissionModel, DataRuleModel
from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.datasource import (
    build_datasource_catalog,
    build_datasource_policy_catalog,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(
    tags=["data_permission"],
    prefix="/ds_permission",
    include_in_schema=False,
)


class PermissionItemPayload(BaseModel):
    id: int | str | None = None
    name: str
    type: str
    ds_id: int
    table_id: int
    expression_tree: str | dict[str, Any] | None = None
    permissions: str | list[dict[str, Any]] | None = None


class PermissionRulePayload(BaseModel):
    id: int | str | None = None
    name: str
    permissions: list[PermissionItemPayload] = Field(default_factory=list)
    users: list[int] = Field(default_factory=list)


def _json_ids(value: str | None, field: str) -> list[int]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} 不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field} 必须是数组")
    try:
        return [int(item) for item in parsed]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 包含非法 ID") from exc


def _serialize_permission(
    permission: DataPermissionModel,
    datasource_names: dict[int, str],
    table_names: dict[int, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": permission.id,
        "name": permission.name,
        "type": permission.type,
        "ds_id": permission.ds_id,
        "ds_name": datasource_names.get(permission.ds_id or 0, ""),
        "table_id": permission.table_id,
        "table_name": table_names.get(permission.table_id or 0, ""),
    }
    if permission.type == "row":
        result["tree"] = permission.expression_tree or "{}"
        result["expression_tree"] = permission.expression_tree or "{}"
    else:
        try:
            result["permission_list"] = json.loads(permission.permissions or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"权限 {permission.id} 的列权限配置不是合法 JSON"
            ) from exc
        result["permissions"] = permission.permissions or "[]"
    return result


def _list_rules(
    session: SessionDep,
    workspace_id: int,
    rule_id: int | None = None,
) -> list[dict[str, Any]]:
    statement = select(DataRuleModel).where(
        col(DataRuleModel.oid) == workspace_id,
        col(DataRuleModel.enable).is_(True),
    )
    if rule_id is not None:
        statement = statement.where(col(DataRuleModel.id) == rule_id)
    rules = session.exec(
        statement.order_by(col(DataRuleModel.create_time).desc())
    ).all()
    permission_ids = {
        permission_id
        for rule in rules
        for permission_id in _json_ids(rule.permission_list, "permission_list")
    }
    permissions = (
        session.exec(
            select(DataPermissionModel).where(
                col(DataPermissionModel.id).in_(permission_ids),
                col(DataPermissionModel.enable).is_(True),
            )
        ).all()
        if permission_ids
        else []
    )
    permission_by_id = {
        permission.id: permission
        for permission in permissions
        if permission.id is not None
    }
    datasource_ids = {
        item.ds_id for item in permissions if item.ds_id is not None
    }
    table_ids = {
        item.table_id for item in permissions if item.table_id is not None
    }
    datasource_names = {
        int(item.id): item.name
        for item in build_datasource_catalog(session).list_for_workspace(
            workspace_id,
            sorted(datasource_ids),
        )
    }
    policy_catalog = build_datasource_policy_catalog(session)
    table_names: dict[int, str] = {}
    for datasource_id in datasource_ids:
        schema = policy_catalog.get_policy_schema(
            workspace_id,
            datasource_id,
        )
        if schema is None:
            continue
        table_names.update(
            {
                table.id: table.name
                for table in schema.tables
                if table.id in table_ids
            }
        )

    return [
        {
            "id": rule.id,
            "name": rule.name,
            "permissions": [
                _serialize_permission(
                    permission_by_id[permission_id],
                    datasource_names,
                    table_names,
                )
                for permission_id in _json_ids(
                    rule.permission_list,
                    "permission_list",
                )
                if permission_id in permission_by_id
            ],
            "users": _json_ids(rule.user_list, "user_list"),
        }
        for rule in rules
    ]


@router.post("/list")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def query(
    session: SessionDep,
    current_user: CurrentUser,
) -> list[dict[str, Any]]:
    return _list_rules(session, current_user.oid)


@router.post("/get/{id}")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def get_one(
    session: SessionDep,
    current_user: CurrentUser,
    id: int,
) -> dict[str, Any]:
    rows = _list_rules(session, current_user.oid, id)
    if not rows:
        raise ValueError(f"权限规则组不存在: {id}")
    return rows[0]


@router.post("/save")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def save(
    session: SessionDep,
    current_user: CurrentUser,
    payload: PermissionRulePayload,
) -> None:
    name = payload.name.strip()
    if not name:
        raise ValueError("权限规则组名称不能为空")
    if len(set(payload.users)) != len(payload.users):
        raise ValueError("权限规则组用户不能重复")

    rule: DataRuleModel | None = None
    old_permission_ids: set[int] = set()
    if payload.id is not None and payload.id != "":
        try:
            rule_id = int(payload.id)
        except (TypeError, ValueError) as exc:
            raise ValueError("权限规则组 ID 不合法") from exc
        rule = session.get(DataRuleModel, rule_id)
        if rule is None or rule.oid != current_user.oid:
            raise ValueError(f"权限规则组不存在: {rule_id}")
        old_permission_ids = set(
            _json_ids(rule.permission_list, "permission_list")
        )

    saved_permission_ids: list[int] = []
    policy_catalog = build_datasource_policy_catalog(session)
    for item in payload.permissions:
        if item.type not in {"row", "column"}:
            raise ValueError(f"不支持的权限类型: {item.type}")
        policy_schema = policy_catalog.get_policy_schema(
            current_user.oid,
            item.ds_id,
            table_id=item.table_id,
        )
        if policy_schema is None or not policy_schema.tables:
            raise ValueError("权限引用的数据源或数据表不属于当前工作区")

        permission: DataPermissionModel | None = None
        if item.id is not None and item.id != "":
            try:
                permission_id = int(item.id)
            except (TypeError, ValueError):
                permission_id = 0
            if permission_id in old_permission_ids:
                permission = session.get(DataPermissionModel, permission_id)
        if permission is None:
            permission = DataPermissionModel(type=item.type)

        expression_tree = (
            item.expression_tree
            if isinstance(item.expression_tree, str)
            else json.dumps(item.expression_tree or {}, ensure_ascii=False)
        )
        permissions = (
            item.permissions
            if isinstance(item.permissions, str)
            else json.dumps(item.permissions or [], ensure_ascii=False)
        )
        permission.name = item.name.strip()
        permission.enable = True
        permission.type = item.type
        permission.ds_id = item.ds_id
        permission.table_id = item.table_id
        permission.expression_tree = (
            expression_tree if item.type == "row" else "{}"
        )
        permission.permissions = permissions if item.type == "column" else "[]"
        permission.white_list_user = "[]"
        session.add(permission)
        session.flush()
        if permission.id is None:
            raise RuntimeError("权限规则保存后未生成 ID")
        saved_permission_ids.append(permission.id)

    for stale_id in old_permission_ids - set(saved_permission_ids):
        stale = session.get(DataPermissionModel, stale_id)
        if stale is not None:
            session.delete(stale)

    if rule is None:
        rule = DataRuleModel(name=name, oid=current_user.oid)
    rule.name = name
    rule.enable = True
    rule.permission_list = json.dumps(saved_permission_ids)
    rule.user_list = json.dumps(payload.users)
    rule.white_list_user = "[]"
    session.add(rule)
    session.commit()


@router.post("/delete/{id}")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def delete(
    session: SessionDep,
    current_user: CurrentUser,
    id: int,
) -> None:
    rule = session.get(DataRuleModel, id)
    if rule is None or rule.oid != current_user.oid:
        raise ValueError(f"权限规则组不存在: {id}")
    for permission_id in _json_ids(rule.permission_list, "permission_list"):
        permission = session.get(DataPermissionModel, permission_id)
        if permission is not None:
            session.delete(permission)
    session.delete(rule)
    session.commit()


__all__ = ["router"]
