"""Access Control 用户与工作空间业务规则测试。"""

from unittest.mock import Mock

import pytest

from apps.access_control.errors import (
    CurrentPasswordMismatchError,
    WorkspaceMemberAlreadyExistsError,
    WorkspaceMembershipRequiredError,
    WorkspaceNotFoundError,
)
from apps.access_control.models.dto import (
    UserCreator,
    UserEditor,
    UserRecord,
    WorkspaceRecord,
)
from apps.access_control.services import IdentityWorkspaceService


def _user(*, user_id: int = 2, workspace_id: int = 7) -> UserRecord:
    return UserRecord(
        id=user_id,
        account="member",
        oid=workspace_id,
        name="Member",
        password="hashed-old",
        email="member@example.com",
        status=1,
        origin=0,
        create_time=1,
        language="zh-CN",
    )


def _workspace(workspace_id: int) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=workspace_id, name=f"workspace-{workspace_id}", create_time=1
    )


def _service(repository: Mock) -> IdentityWorkspaceService:
    return IdentityWorkspaceService(
        repository,
        verify_password=lambda plain, hashed: plain == "old" and hashed == "hashed-old",
        hash_password=lambda password: f"hashed-{password}",
        default_password=lambda: "hashed-default",
        normalize_variable_assignments=lambda assignments: assignments or [],
    )


def test_switch_workspace_requires_membership_for_normal_user() -> None:
    repository = Mock()
    repository.get_user.return_value = _user()
    repository.get_workspace.return_value = _workspace(8)
    repository.get_membership_weight.return_value = None
    service = _service(repository)

    with pytest.raises(WorkspaceMembershipRequiredError):
        service.switch_workspace(2, 8)

    repository.set_current_workspace.assert_not_called()


def test_system_admin_can_switch_to_any_existing_workspace() -> None:
    repository = Mock()
    repository.get_user.return_value = _user(user_id=1, workspace_id=1)
    repository.get_workspace.return_value = _workspace(8)
    service = _service(repository)

    service.switch_workspace(1, 8)

    repository.get_membership_weight.assert_not_called()
    repository.set_current_workspace.assert_called_once_with(1, 8)


def test_create_user_rejects_unknown_workspace_before_write() -> None:
    repository = Mock()
    repository.account_exists.return_value = False
    repository.find_missing_workspace_ids.return_value = {99}
    service = _service(repository)
    creator = UserCreator(
        account="new-user",
        oid=0,
        name="New User",
        email="new-user@example.com",
        oid_list=[7, 99],
    )

    with pytest.raises(WorkspaceNotFoundError) as error:
        service.create_user(creator)

    assert error.value.workspace_id == 99
    repository.create_user.assert_not_called()


def test_create_user_normalizes_null_origin_to_local_origin() -> None:
    repository = Mock()
    repository.account_exists.return_value = False
    repository.find_missing_workspace_ids.return_value = set()
    repository.create_user.return_value = _user()
    service = _service(repository)
    creator = UserCreator(
        account="new-user",
        oid=0,
        name="New User",
        email="new-user@example.com",
        origin=None,
        oid_list=[7],
    )

    service.create_user(creator)

    normalized_creator = repository.create_user.call_args.args[0]
    assert normalized_creator.origin == 0
    repository.create_user.assert_called_once_with(normalized_creator, [7])


def test_update_user_preserves_variable_bindings_when_field_is_omitted() -> None:
    repository = Mock()
    repository.get_user.return_value = _user()
    repository.find_missing_workspace_ids.return_value = set()
    repository.update_user.return_value = _user()
    service = _service(repository)
    editor = UserEditor(
        id=2,
        account="member",
        oid=7,
        name="Member",
        email="member@example.com",
        oid_list=[7],
    )

    service.update_user(editor)

    normalized_editor = repository.update_user.call_args.args[0]
    assert "system_variables" not in normalized_editor.model_fields_set


def test_bind_members_rejects_existing_relation_before_write() -> None:
    repository = Mock()
    repository.get_workspace.return_value = _workspace(7)
    repository.find_missing_user_ids.return_value = set()
    repository.find_existing_member_ids.return_value = {3}
    service = _service(repository)

    with pytest.raises(WorkspaceMemberAlreadyExistsError):
        service.bind_members(workspace_id=7, user_ids=[2, 3, 3], weight=1)

    repository.find_existing_member_ids.assert_called_once_with(7, {2, 3})
    repository.bind_members.assert_not_called()


def test_password_update_checks_current_password_before_write() -> None:
    repository = Mock()
    repository.get_user.return_value = _user()
    service = _service(repository)

    with pytest.raises(CurrentPasswordMismatchError):
        service.update_password(2, "wrong", "New-pass1!")

    repository.update_user_password.assert_not_called()


def test_password_update_hashes_valid_new_password() -> None:
    repository = Mock()
    repository.get_user.return_value = _user()
    repository.update_user_password.return_value = _user()
    service = _service(repository)

    service.update_password(2, "old", "New-pass1!")

    repository.update_user_password.assert_called_once_with(2, "hashed-New-pass1!")
