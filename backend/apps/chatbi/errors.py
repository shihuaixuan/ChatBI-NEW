"""ChatBI 领域公共错误（AGENTS.md v2 §7）。

错误码以类内常量/字符串形式集中于各错误类型附近；R1 起新增错误必须定义在本文件，
旧模块保留同一对象的兼容 re-export（台账 E1）。
"""

from __future__ import annotations


class ChatBIError(Exception):
    """ChatBI 领域错误基类。"""


# --- 规划（planning） ---


class ExecutionBindingError(ChatBIError, ValueError):
    """执行请求与会话绑定不一致。"""


class SemanticQueryCompileError(ChatBIError, ValueError):
    """语义查询计划无法编译。"""


class DatasourceSelectionError(ChatBIError, ValueError):
    """数据源选择输入或模型结果不合法。"""


# --- 执行（execution） ---


class QueryResultProjectionError(ChatBIError, ValueError):
    """查询结果标准化或投影输入不合法。"""


class ResultArtifactError(ChatBIError, RuntimeError):
    """结果 Artifact 处理错误基类。"""


class ResultArtifactWriteError(ResultArtifactError):
    """完整结果正文或元数据写入失败。"""


# --- 生成（generation） ---


class SQLGenerationError(ChatBIError, ValueError):
    """SQL 生成输入或模型结果不合法。"""


class DynamicSQLGenerationError(ChatBIError, ValueError):
    """动态 SQL 生成输入不合法。"""


class PermissionSQLGenerationError(ChatBIError, ValueError):
    """权限 SQL 生成输入不合法。"""


class ChartGenerationError(ChatBIError, ValueError):
    """图表生成输入或模型结果不合法。"""


class FinalReplyProjectionError(ChatBIError, ValueError):
    """最终回复投影不满足业务前置条件。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# --- 问题理解（understanding） ---


class QuestionUnderstandingError(ChatBIError, RuntimeError):
    """问题理解阶段失败，调用方应明确终止当前问数流程。"""


class QuestionModelError(ChatBIError, RuntimeError):
    """问题理解模型调用或输出错误。"""

    def __init__(self, code: str, stage: str) -> None:
        self.code = code
        self.stage = stage
        super().__init__(f"{code}:{stage}")


class QuestionModelCallError(QuestionModelError):
    """问题理解模型调用失败。"""


class QuestionModelOutputError(QuestionModelError):
    """问题理解模型输出不符合 JSON 对象契约。"""


__all__ = [
    "ChatBIError",
    "ChartGenerationError",
    "DatasourceSelectionError",
    "DynamicSQLGenerationError",
    "ExecutionBindingError",
    "FinalReplyProjectionError",
    "PermissionSQLGenerationError",
    "QueryResultProjectionError",
    "SQLGenerationError",
    "QuestionModelCallError",
    "QuestionModelError",
    "QuestionModelOutputError",
    "QuestionUnderstandingError",
    "ResultArtifactError",
    "ResultArtifactWriteError",
    "SemanticQueryCompileError",
]
