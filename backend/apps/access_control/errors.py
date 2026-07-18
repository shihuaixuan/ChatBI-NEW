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

