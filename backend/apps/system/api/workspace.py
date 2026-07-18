"""XPack 工作空间接口兼容入口。"""

from apps.access_control.cache import clear_user_cache
from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.errors import AccessControlError
from apps.access_control.http_error_mapping import raise_identity_http_error
from apps.access_control.models.dto import UserWsEditor
from common.core.deps import SessionDep, Trans


async def edit(session: SessionDep, trans: Trans, editor: UserWsEditor) -> None:
    """XPack 保留的成员权重入口，业务规则由 Access Control 执行。"""
    if not editor.oid or not editor.uid:
        raise Exception(trans("i18n_miss_args", key="[oid, uid]"))
    try:
        build_identity_workspace_service(session).update_member_weight(
            workspace_id=editor.oid,
            user_id=editor.uid,
            weight=editor.weight,
        )
    except AccessControlError as exc:
        raise_identity_http_error(exc, trans)
    await clear_user_cache(editor.uid)

__all__ = ["edit"]
