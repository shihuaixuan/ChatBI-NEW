from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.execution import NodeExecutionResult
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.runtime.router import ConditionDecision


class SqlValidCondition:
    """SQL 校验通过时才允许进入权限节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        valid = bool(context.variables.get("sql_validation", {}).get("valid"))
        return ConditionDecision(
            matched=valid,
            reason_code="SQL_VALID" if valid else "SQL_INVALID",
            reason_summary="SQL 校验通过" if valid else "SQL 校验未通过",
        )


class PermissionAllowedCondition:
    """权限允许时才允许进入 SQL 执行节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        allowed = bool(context.variables.get("permission", {}).get("allowed"))
        return ConditionDecision(
            matched=allowed,
            reason_code="PERMISSION_ALLOWED" if allowed else "PERMISSION_DENIED",
            reason_summary="权限校验通过" if allowed else "权限校验拒绝",
        )


def register_chatbi_conditions(registry: ConditionRegistry) -> None:
    """注册 ChatBI 最小图使用的确定性条件。"""

    registry.register("sql.valid", SqlValidCondition())
    registry.register("permission.allowed", PermissionAllowedCondition())
