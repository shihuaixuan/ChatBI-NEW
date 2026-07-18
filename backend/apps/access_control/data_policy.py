"""数据策略公开入口和工作流提供者。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from apps.access_control.composition import (
    build_data_policy_service,
    build_identity_workspace_service,
)
from apps.access_control.errors import (
    AccessControlError,
    DataPolicyConfigurationError,
    UserNotFoundError,
)
from apps.access_control.models.dto import DataPolicy, DataPolicySubject, UserInfoDTO


def requires_data_policy(user: UserInfoDTO) -> bool:
    return not user.isAdmin


def resolve_data_policy(
    session: Session,
    user: UserInfoDTO,
    datasource_id: int,
    *,
    table_names: list[str] | None = None,
    table_id: int | None = None,
) -> DataPolicy:
    return build_data_policy_service(session).resolve(
        DataPolicySubject(
            user_id=user.id,
            workspace_id=user.oid,
            is_system_admin=user.isAdmin,
            name=user.name,
            account=user.account,
            email=user.email,
            variable_assignments=user.system_variables or [],
        ),
        datasource_id,
        table_names=table_names,
        table_id=table_id,
    )


class SessionDataPolicyProvider:
    """为 ChatBI 工作流按请求身份读取真实数据策略。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            user_id = self._required_id(payload.get("user_id"), "USER_ID")
            workspace_id = self._required_id(payload.get("tenant_id"), "WORKSPACE_ID")
            datasource_id = self._required_id(
                payload.get("datasource_id"),
                "DATASOURCE_ID",
            )
            with self._session_factory() as session:
                user = build_identity_workspace_service(session).get_user_info(user_id)
                if user is None:
                    raise UserNotFoundError(user_id)
                if not user.isAdmin and user.oid != workspace_id:
                    return self._denied("ACCESS_CONTROL_WORKSPACE_MISMATCH")
                policy = resolve_data_policy(session, user, datasource_id)
                return policy.model_dump(mode="json")
        except AccessControlError as exc:
            return self._denied(str(exc))

    @staticmethod
    def _required_id(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise DataPolicyConfigurationError(f"{field}_REQUIRED")
        try:
            normalized = int(value)
        except ValueError as exc:
            raise DataPolicyConfigurationError(f"{field}_REQUIRED") from exc
        if normalized <= 0:
            raise DataPolicyConfigurationError(f"{field}_REQUIRED")
        return normalized

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return DataPolicy(
            allowed=False,
            reason=reason,
            error_code="data_policy_denied",
        ).model_dump(mode="json")
