"""Access Control 领域错误。"""


class AccessControlError(Exception):
    """Access Control 领域错误基类。"""


class SystemAdminRequiredError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_SYSTEM_ADMIN_REQUIRED")


class WorkspaceAdminRequiredError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_WORKSPACE_ADMIN_REQUIRED")


class UnsupportedPermissionRoleError(AccessControlError):
    def __init__(self, role: str) -> None:
        super().__init__(f"ACCESS_CONTROL_UNSUPPORTED_ROLE:{role}")


class ResourceContextRequiredError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_RESOURCE_CONTEXT_REQUIRED")


class InvalidResourceReferenceError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_RESOURCE_REFERENCE_INVALID")


class ResourcePermissionDeniedError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_RESOURCE_PERMISSION_DENIED")


class UnsupportedResourceTypeError(AccessControlError):
    def __init__(self, resource_type: str) -> None:
        super().__init__(f"ACCESS_CONTROL_UNSUPPORTED_RESOURCE_TYPE:{resource_type}")


class UserNotFoundError(AccessControlError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"ACCESS_CONTROL_USER_NOT_FOUND:{user_id}")


class UserAccountExistsError(AccessControlError):
    def __init__(self, account: str) -> None:
        self.account = account
        super().__init__(f"ACCESS_CONTROL_USER_ACCOUNT_EXISTS:{account}")


class UserAccountImmutableError(AccessControlError):
    def __init__(self, account: str) -> None:
        self.account = account
        super().__init__(f"ACCESS_CONTROL_USER_ACCOUNT_IMMUTABLE:{account}")


class InvalidUserEmailError(AccessControlError):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"ACCESS_CONTROL_USER_EMAIL_INVALID:{email}")


class InvalidUserPasswordError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_USER_PASSWORD_INVALID")


class CurrentPasswordMismatchError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_CURRENT_PASSWORD_MISMATCH")


class UnsupportedUserLanguageError(AccessControlError):
    def __init__(self, language: str) -> None:
        self.language = language
        super().__init__(f"ACCESS_CONTROL_USER_LANGUAGE_UNSUPPORTED:{language}")


class UnsupportedUserStatusError(AccessControlError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"ACCESS_CONTROL_USER_STATUS_UNSUPPORTED:{status}")


class WorkspaceNotFoundError(AccessControlError):
    def __init__(self, workspace_id: int) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"ACCESS_CONTROL_WORKSPACE_NOT_FOUND:{workspace_id}")


class WorkspaceMembershipRequiredError(AccessControlError):
    def __init__(self, workspace_id: int, workspace_name: str) -> None:
        self.workspace_id = workspace_id
        self.workspace_name = workspace_name
        super().__init__(
            f"ACCESS_CONTROL_WORKSPACE_MEMBERSHIP_REQUIRED:{workspace_id}"
        )


class DefaultWorkspaceCannotDeleteError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_DEFAULT_WORKSPACE_CANNOT_DELETE")


class WorkspaceMemberNotFoundError(AccessControlError):
    def __init__(self, workspace_id: int) -> None:
        self.workspace_id = workspace_id
        super().__init__(f"ACCESS_CONTROL_WORKSPACE_MEMBER_NOT_FOUND:{workspace_id}")


class WorkspaceMemberAlreadyExistsError(AccessControlError):
    def __init__(self, workspace_id: int, user_id: int) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        super().__init__(
            f"ACCESS_CONTROL_WORKSPACE_MEMBER_ALREADY_EXISTS:{workspace_id}:{user_id}"
        )
