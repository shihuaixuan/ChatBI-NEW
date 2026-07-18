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
        super().__init__(f"ACCESS_CONTROL_WORKSPACE_MEMBERSHIP_REQUIRED:{workspace_id}")


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


class InvalidCredentialsError(AccessControlError):
    def __init__(self) -> None:
        super().__init__("ACCESS_CONTROL_INVALID_CREDENTIALS")


class UserInactiveError(AccessControlError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"ACCESS_CONTROL_USER_INACTIVE:{user_id}")


class UserWorkspaceRequiredError(AccessControlError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"ACCESS_CONTROL_USER_WORKSPACE_REQUIRED:{user_id}")


class LocalLoginRequiredError(AccessControlError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"ACCESS_CONTROL_LOCAL_LOGIN_REQUIRED:{user_id}")


class ApiKeyLimitExceededError(AccessControlError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"ACCESS_CONTROL_API_KEY_LIMIT_EXCEEDED:{limit}")


class ApiKeyNotFoundError(AccessControlError):
    def __init__(self, api_key_id: int | None = None) -> None:
        self.api_key_id = api_key_id
        super().__init__(f"ACCESS_CONTROL_API_KEY_NOT_FOUND:{api_key_id}")


class ApiKeyDisabledError(AccessControlError):
    def __init__(self, access_key: str) -> None:
        self.access_key = access_key
        super().__init__("ACCESS_CONTROL_API_KEY_DISABLED")


class ApiKeyOwnershipError(AccessControlError):
    def __init__(self, action: str) -> None:
        self.action = action
        super().__init__(f"ACCESS_CONTROL_API_KEY_OWNERSHIP_REQUIRED:{action}")


class AccessVariableNotFoundError(AccessControlError):
    def __init__(self, variable_id: int) -> None:
        self.variable_id = variable_id
        super().__init__(f"ACCESS_CONTROL_VARIABLE_NOT_FOUND:{variable_id}")


class AccessVariableNameExistsError(AccessControlError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"ACCESS_CONTROL_VARIABLE_NAME_EXISTS:{name}")


class AccessVariableDefinitionError(AccessControlError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"ACCESS_CONTROL_VARIABLE_INVALID:{reason}")


class AccessVariableSystemMutationError(AccessControlError):
    def __init__(self, variable_id: int) -> None:
        super().__init__(f"ACCESS_CONTROL_SYSTEM_VARIABLE_IMMUTABLE:{variable_id}")


class UserVariableAssignmentError(AccessControlError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"ACCESS_CONTROL_USER_VARIABLE_INVALID:{reason}")


class DataPolicyConfigurationError(AccessControlError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"ACCESS_CONTROL_DATA_POLICY_INVALID:{reason}")


class DataPolicyDatasourceNotFoundError(AccessControlError):
    def __init__(self, datasource_id: int, workspace_id: int) -> None:
        super().__init__(
            f"ACCESS_CONTROL_DATA_POLICY_DATASOURCE_NOT_FOUND:"
            f"{datasource_id}:{workspace_id}"
        )
