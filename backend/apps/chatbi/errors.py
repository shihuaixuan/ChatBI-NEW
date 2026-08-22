"""ChatBI 领域公共错误（AGENTS.md v2 §7）。

错误码以类内常量/字符串形式集中于各错误类型附近；R1 起新增错误必须定义在本文件，
旧模块保留同一对象的兼容 re-export（台账 E1）。
"""

from __future__ import annotations

from typing import Any


class ChatBIError(Exception):
    """ChatBI 领域错误基类。"""


# --- 规划（planning） ---


class ExecutionBindingError(ChatBIError, ValueError):
    """执行请求与会话绑定不一致。"""


class DatasourceSelectionError(ChatBIError, ValueError):
    """数据源选择输入或模型结果不合法。"""


class LimitedMultiStepDecompositionError(ChatBIError, RuntimeError):
    """受限多步模型调用或草案校验失败。"""

    MODEL_CALL_FAILED = "LIMITED_MULTISTEP_MODEL_CALL_FAILED"
    OUTPUT_INVALID = "LIMITED_MULTISTEP_OUTPUT_INVALID"
    ASSET_REF_NOT_ALLOWED = "LIMITED_MULTISTEP_ASSET_REF_NOT_ALLOWED"
    TIME_ROLE_NOT_ALLOWED = "LIMITED_MULTISTEP_TIME_ROLE_NOT_ALLOWED"
    OPERATION_NOT_ALLOWED = "LIMITED_MULTISTEP_OPERATION_NOT_ALLOWED"
    BUDGET_EXCEEDED = "LIMITED_MULTISTEP_BUDGET_EXCEEDED"

    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(code)


class ResearchRequirementError(ChatBIError, ValueError):
    """Research 执行范围无法形成完整、可信的需求。"""

    TARGET_METRIC_REQUIRED = "RESEARCH_TARGET_METRIC_REQUIRED"
    TARGET_METRIC_SINGLE_MODEL_REQUIRED = (
        "RESEARCH_TARGET_METRIC_SINGLE_MODEL_REQUIRED"
    )
    TARGET_METRIC_NOT_FOUND = "RESEARCH_TARGET_METRIC_NOT_FOUND"
    DIMENSION_NOT_FOUND = "RESEARCH_DIMENSION_NOT_FOUND"
    DIMENSION_MODEL_MISMATCH = "RESEARCH_DIMENSION_MODEL_MISMATCH"
    TIME_ROLE_DUPLICATED = "RESEARCH_TIME_ROLE_DUPLICATED"
    TIME_CONTEXT_REQUIRED = "RESEARCH_TIME_CONTEXT_REQUIRED"
    TIME_DIMENSION_REQUIRED = "RESEARCH_TIME_DIMENSION_REQUIRED"
    TIME_RANGE_UNRESOLVED = "RESEARCH_TIME_RANGE_UNRESOLVED"
    SCHEMA_FINGERPRINT_REQUIRED = "RESEARCH_SCHEMA_FINGERPRINT_REQUIRED"
    SCOPE_EMPTY = "RESEARCH_SCOPE_EMPTY"
    GOVERNANCE_CONTRACT_INVALID = "RESEARCH_GOVERNANCE_CONTRACT_INVALID"

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResearchExecutionError(ChatBIError, RuntimeError):
    """Research 动态循环的模型决策、动作或预算不满足执行条件。"""

    MODEL_CALL_FAILED = "RESEARCH_MODEL_CALL_FAILED"
    POLICY_OUTPUT_INVALID = "RESEARCH_POLICY_OUTPUT_INVALID"
    ACTION_NOT_ALLOWED = "RESEARCH_ACTION_NOT_ALLOWED"
    ACTION_SCOPE_INVALID = "RESEARCH_ACTION_SCOPE_INVALID"
    ACTION_DUPLICATED = "RESEARCH_ACTION_DUPLICATED"
    ACTION_SOURCE_UNKNOWN = "RESEARCH_ACTION_SOURCE_UNKNOWN"
    ACTION_SOURCE_COLUMN_UNKNOWN = "RESEARCH_ACTION_SOURCE_COLUMN_UNKNOWN"
    ACTION_SOURCE_ROW_UNKNOWN = "RESEARCH_ACTION_SOURCE_ROW_UNKNOWN"
    ACTION_MATERIALIZATION_FAILED = "RESEARCH_ACTION_MATERIALIZATION_FAILED"
    BUDGET_EXHAUSTED = "RESEARCH_BUDGET_EXHAUSTED"
    COMPARISON_REQUIRED = "RESEARCH_COMPARISON_REQUIRED"
    EVIDENCE_RESULT_INVALID = "RESEARCH_EVIDENCE_RESULT_INVALID"
    HYPOTHESIS_INVALID = "RESEARCH_HYPOTHESIS_INVALID"
    HYPOTHESIS_EVIDENCE_REQUIRED = "RESEARCH_HYPOTHESIS_EVIDENCE_REQUIRED"
    ACTION_BATCH_DEPENDENCY_INVALID = "RESEARCH_ACTION_BATCH_DEPENDENCY_INVALID"
    FINISH_CONDITION_INVALID = "RESEARCH_FINISH_CONDITION_INVALID"
    REPORT_INVALID = "RESEARCH_REPORT_INVALID"
    HIERARCHY_NOT_AVAILABLE = "RESEARCH_HIERARCHY_NOT_AVAILABLE"

    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(code)


class AgentActionError(ChatBIError, ValueError):
    """Agent 提出的动作不满足当前可信进展。"""

    ACTION_NOT_AVAILABLE = "agent_action_not_available"


# --- 执行（execution） ---


class QueryResultProjectionError(ChatBIError, ValueError):
    """查询结果标准化或投影输入不合法。"""


class ResultArtifactError(ChatBIError, RuntimeError):
    """结果 Artifact 处理错误基类。"""


class ResultArtifactWriteError(ResultArtifactError):
    """完整结果正文或元数据写入失败。"""


class ResultArtifactReadError(ResultArtifactError):
    """完整结果正文读取失败或引用归属不匹配。"""

    READ_FAILED = "RESULT_ARTIFACT_READ_FAILED"
    OWNERSHIP_MISMATCH = "RESULT_ARTIFACT_OWNERSHIP_MISMATCH"


class SemanticQueryRuntimeError(ChatBIError, RuntimeError):
    """Semantic Query Runtime 的预期业务失败及其稳定错误元数据。"""

    def __init__(
        self,
        code: str,
        *,
        failure_stage: str,
        retryable: bool = False,
        parameter_retryable: bool = False,
        same_parameter_retryable: bool = False,
        capability_gap: bool = False,
        sql_escalation_allowed: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.failure_stage = failure_stage
        self.retryable = retryable
        self.parameter_retryable = parameter_retryable
        self.same_parameter_retryable = same_parameter_retryable
        self.capability_gap = capability_gap
        self.sql_escalation_allowed = sql_escalation_allowed
        self.details = dict(details or {})
        super().__init__(code)


class SemanticQueryBoundaryError(ChatBIError, ValueError):
    """Semantic Query 的冻结运行边界不满足。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SemanticQueryBuildError(ChatBIError, ValueError):
    """Semantic Query 无法转换为受治理执行规格。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SemanticQueryProjectionError(ChatBIError, ValueError):
    """执行结果无法投影为 Research 证据。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# --- 生成（generation） ---


class SQLGenerationError(ChatBIError, ValueError):
    """SQL 生成输入或模型结果不合法。"""


class DynamicSQLGenerationError(ChatBIError, ValueError):
    """动态 SQL 生成输入不合法。"""


class PermissionSQLGenerationError(ChatBIError, ValueError):
    """权限 SQL 生成输入不合法。"""


class ChartGenerationError(ChatBIError, ValueError):
    """图表生成输入或模型结果不合法。"""


class AgentFinalizationError(ChatBIError, ValueError):
    """Agent 最终分析回复或图表配置生成失败。"""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class FinalReplyProjectionError(ChatBIError, ValueError):
    """最终回复投影不满足业务前置条件。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# --- 问题理解（understanding） ---


class QuestionUnderstandingError(ChatBIError, RuntimeError):
    """问题理解阶段失败，调用方应明确终止当前问数流程。"""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.details = dict(details or {})
        super().__init__(message)


class TemporalInterpretationError(ChatBIError, RuntimeError):
    """旁路时间理解失败；错误和已发生的模型用量必须显式保留。"""

    def __init__(
        self,
        code: str,
        *,
        usage_metadata: dict[str, int] | None = None,
    ) -> None:
        self.code = code
        self.usage_metadata = usage_metadata or {}
        super().__init__(code)


class QuestionModelError(ChatBIError, RuntimeError):
    """问题理解模型调用或输出错误。"""

    def __init__(
        self,
        code: str,
        stage: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.details = dict(details or {})
        super().__init__(f"{code}:{stage}")


class QuestionModelCallError(QuestionModelError):
    """问题理解模型调用失败。"""


class QuestionModelOutputError(QuestionModelError):
    """问题理解模型输出不符合 JSON 对象契约。"""


# --- 语义澄清（semantic clarification） ---


class SemanticClarificationError(ChatBIError, RuntimeError):
    """语义资产澄清回答无法通过服务端候选校验。"""

    SCOPE_REQUIRED = "SEMANTIC_CLARIFICATION_SCOPE_REQUIRED"
    RETRIEVAL_MISMATCH = "SEMANTIC_CLARIFICATION_RETRIEVAL_MISMATCH"
    OPTIONS_REQUIRED = "SEMANTIC_CLARIFICATION_OPTIONS_REQUIRED"
    STRUCTURED_SELECTION_REQUIRED = (
        "SEMANTIC_CLARIFICATION_STRUCTURED_SELECTION_REQUIRED"
    )
    OPTION_NOT_FOUND = "SEMANTIC_CLARIFICATION_OPTION_NOT_FOUND"
    SNAPSHOT_REQUIRED = "SEMANTIC_CLARIFICATION_SNAPSHOT_REQUIRED"
    PAYLOAD_REQUIRED = "SEMANTIC_CLARIFICATION_PAYLOAD_REQUIRED"

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


__all__ = [
    "AgentActionError",
    "ChatBIError",
    "ChartGenerationError",
    "DatasourceSelectionError",
    "DynamicSQLGenerationError",
    "ExecutionBindingError",
    "FinalReplyProjectionError",
    "LimitedMultiStepDecompositionError",
    "ResearchExecutionError",
    "ResearchRequirementError",
    "PermissionSQLGenerationError",
    "QueryResultProjectionError",
    "SQLGenerationError",
    "SemanticClarificationError",
    "TemporalInterpretationError",
    "QuestionModelCallError",
    "QuestionModelError",
    "QuestionModelOutputError",
    "QuestionUnderstandingError",
    "ResultArtifactError",
    "ResultArtifactReadError",
    "ResultArtifactWriteError",
    "SemanticQueryRuntimeError",
]
