"""将 HTTP 请求适配为统一授权调用。"""

import re
from collections.abc import Awaitable, Callable
from functools import wraps
from inspect import signature
from typing import ParamSpec, TypeVar, cast

from fastapi import HTTPException

from apps.access_control.composition import build_authorization_service
from apps.access_control.errors import (
    InvalidResourceReferenceError,
    ResourceContextRequiredError,
    ResourcePermissionDeniedError,
    SystemAdminRequiredError,
    UnsupportedPermissionRoleError,
    UnsupportedResourceTypeError,
    WorkspaceAdminRequiredError,
)
from apps.access_control.models.dto import AuthorizationSubject, SqlbotPermission
from common.utils.locale import I18n

from .request_context import RequestContext

P = ParamSpec("P")
R = TypeVar("R")
_POSITIONAL_EXPRESSION = re.compile(r"args\[(\d+)]")
_authorization_service = build_authorization_service()
i18n = I18n()


def resolve_resource_reference(
    func: Callable[..., object],
    args: tuple[object, ...],
    kwargs: dict[str, object],
    expression: str,
) -> object:
    """按兼容表达式读取接口参数，缺失时明确报错。"""

    bound_args = signature(func).bind_partial(*args, **kwargs)
    bound_args.apply_defaults()

    if match := _POSITIONAL_EXPRESSION.fullmatch(expression):
        index = int(match.group(1))
        if index >= len(bound_args.args):
            raise ResourceContextRequiredError()
        return bound_args.args[index]

    parts = expression.split(".")
    if not parts[0] or parts[0] not in bound_args.arguments:
        raise ResourceContextRequiredError()
    value = bound_args.arguments[parts[0]]
    for part in parts[1:]:
        if not part or value is None or not hasattr(value, part):
            raise ResourceContextRequiredError()
        value = getattr(value, part)
    return value


def require_permissions(
    permission: SqlbotPermission,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """保留现有声明形式，将授权判断统一委托给 Service。"""

    requirement = permission.to_requirement()

    def decorator(
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            request = RequestContext.get_request()
            current_user = getattr(request.state, "current_user", None)
            if current_user is None:
                raise HTTPException(status_code=401, detail="用户未认证")

            workspace_id = getattr(current_user, "oid", None)
            workspace_weight = getattr(current_user, "weight", None)
            if (
                isinstance(workspace_id, bool)
                or not isinstance(workspace_id, int)
                or isinstance(workspace_weight, bool)
                or not isinstance(workspace_weight, int)
            ):
                raise HTTPException(status_code=401, detail="用户未认证")

            try:
                resource: object
                if requirement.resource_expression is None:
                    resource = None
                else:
                    resource = resolve_resource_reference(
                        cast(Callable[..., object], func),
                        cast(tuple[object, ...], args),
                        cast(dict[str, object], kwargs),
                        requirement.resource_expression,
                    )
                await _authorization_service.authorize(
                    subject=AuthorizationSubject(
                        workspace_id=workspace_id,
                        is_system_admin=bool(getattr(current_user, "isAdmin", False)),
                        workspace_weight=workspace_weight,
                    ),
                    requirement=requirement,
                    resource=resource,
                )
            except SystemAdminRequiredError as exc:
                raise Exception(i18n(request)("i18n_permission.only_admin")) from exc
            except WorkspaceAdminRequiredError as exc:
                raise Exception(i18n(request)("i18n_permission.only_ws_admin")) from exc
            except UnsupportedPermissionRoleError as exc:
                raise RuntimeError(str(exc)) from exc
            except (
                InvalidResourceReferenceError,
                ResourceContextRequiredError,
                ResourcePermissionDeniedError,
                UnsupportedResourceTypeError,
            ) as exc:
                raise Exception(
                    i18n(request)("i18n_permission.permission_resource_limit")
                ) from exc

            return await func(*args, **kwargs)

        return wrapper

    return decorator
