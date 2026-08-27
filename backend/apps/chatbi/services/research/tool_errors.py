"""Research 工具错误到统一 ``ExecutionError`` 的映射。"""

from __future__ import annotations

from pydantic import ValidationError

from apps.chatbi.errors import ResearchToolExecutionError
from apps.chatbi.models.dto.research_agent import (
    ExecutionError,
    ResearchExecutionErrorStage,
    ToolErrorCode,
)


def execution_error(
    *,
    code: str,
    stage: ResearchExecutionErrorStage,
    message: str,
    retryable: bool = False,
    parameter_retryable: bool = False,
    same_parameter_retryable: bool = False,
) -> ExecutionError:
    """构造并校验统一错误负载。"""

    return ExecutionError(
        code=code,
        stage=stage,
        message=message[:2_000],
        retryable=retryable,
        parameter_retryable=parameter_retryable,
        same_parameter_retryable=same_parameter_retryable,
    )


def map_research_tool_error(
    error: Exception,
    *,
    default_stage: ResearchExecutionErrorStage = ResearchExecutionErrorStage.VALIDATION,
) -> ExecutionError:
    """将已知工具错误统一为可持久化的 ``ExecutionError``。

    这里只处理能够明确判断的异常类型；未知程序异常不在这里被吞掉，仍由宿主
    失败处理和日志系统接管。
    """

    if isinstance(error, ResearchToolExecutionError):
        return execution_error(
            code=error.code,
            stage=_stage_for_tool_error(error.code, default_stage),
            message=str(error),
            retryable=error.retryable,
            parameter_retryable=error.parameter_retryable,
            same_parameter_retryable=error.same_parameter_retryable,
        )
    if isinstance(error, ValidationError):
        return execution_error(
            code=ToolErrorCode.INVALID_REQUEST.value,
            stage=default_stage,
            message=f"参数校验失败：{error}",
            retryable=True,
            parameter_retryable=True,
        )
    if isinstance(error, PermissionError):
        return execution_error(
            code=ToolErrorCode.PERMISSION_DENIED.value,
            stage=ResearchExecutionErrorStage.PERMISSION,
            message=str(error) or "当前权限不允许执行该工具",
        )
    if isinstance(error, TimeoutError):
        return execution_error(
            code=ToolErrorCode.EXECUTION_TIMEOUT.value,
            stage=ResearchExecutionErrorStage.EXECUTION,
            message=str(error) or "工具执行超时",
            retryable=True,
        )
    if isinstance(error, ValueError):
        return execution_error(
            code=ResearchToolExecutionError.PREPARE_FAILED,
            stage=default_stage,
            message=str(error) or "工具参数或领域条件不满足",
            retryable=True,
            parameter_retryable=True,
        )
    raise TypeError("RESEARCH_UNKNOWN_TOOL_ERROR") from error


def _stage_for_tool_error(
    code: str,
    default_stage: ResearchExecutionErrorStage,
) -> ResearchExecutionErrorStage:
    stages = {
        ResearchToolExecutionError.TOOL_NOT_FOUND: ResearchExecutionErrorStage.VISIBILITY,
        ResearchToolExecutionError.TOOL_NOT_VISIBLE: ResearchExecutionErrorStage.VISIBILITY,
        ResearchToolExecutionError.PARSING_FAILED: ResearchExecutionErrorStage.PARSING,
        ResearchToolExecutionError.RUN_NOT_EXECUTABLE: ResearchExecutionErrorStage.VALIDATION,
        ResearchToolExecutionError.RUN_CANCELLED: ResearchExecutionErrorStage.EXECUTION,
        ResearchToolExecutionError.ARGUMENTS_INVALID: ResearchExecutionErrorStage.VALIDATION,
        ResearchToolExecutionError.PREPARE_FAILED: ResearchExecutionErrorStage.VALIDATION,
        ResearchToolExecutionError.PERMISSION_DENIED: ResearchExecutionErrorStage.PERMISSION,
        ResearchToolExecutionError.PLANNING_FAILED: ResearchExecutionErrorStage.PLANNING,
        ResearchToolExecutionError.COMPILATION_FAILED: ResearchExecutionErrorStage.COMPILATION,
        ResearchToolExecutionError.EXECUTION_FAILED: ResearchExecutionErrorStage.EXECUTION,
        ResearchToolExecutionError.RESULT_INVALID: ResearchExecutionErrorStage.EXECUTION,
        ResearchToolExecutionError.PERSISTENCE_FAILED: ResearchExecutionErrorStage.PERSISTENCE,
        ResearchToolExecutionError.BUDGET_EXHAUSTED: ResearchExecutionErrorStage.BUDGET,
        ResearchToolExecutionError.ACTION_DUPLICATED: ResearchExecutionErrorStage.VALIDATION,
        ResearchToolExecutionError.ACTION_BATCH_INVALID: ResearchExecutionErrorStage.VALIDATION,
        ResearchToolExecutionError.COMPLETION_FAILED: ResearchExecutionErrorStage.COMPLETION,
    }
    return stages.get(code, default_stage)


__all__ = ["execution_error", "map_research_tool_error"]
