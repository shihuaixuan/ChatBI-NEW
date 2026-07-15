from apps.chatbi_capabilities.schemas import ToolResult


class PermissionTool:
    name = "permission.apply"

    def run(self, payload: dict) -> ToolResult:
        sql = payload.get("sql")
        if not sql:
            return ToolResult(success=False, error_code="empty_sql", message="SQL 不能为空")
        # 当前先透传，保留接入行列权限改写的位置。
        return ToolResult(success=True, payload={"sql": sql})
