from typing import Any, Literal

from pydantic import BaseModel, Field


class QuestionClassificationInput(BaseModel):
    """问题分类节点输入。"""

    question: str
    tenant_id: int
    user_id: int
    dataset_id: int
    conversation_context: dict[str, Any] = Field(default_factory=dict)


class QuestionClassificationOutput(BaseModel):
    """问题分类节点输出。"""

    category: Literal["forbidden", "chitchat", "data", "followup"]
    reason: str
    risk_level: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(default=0.0, ge=0, le=1)


class QuestionRewriteInput(BaseModel):
    """问题重写节点输入。"""

    question: str
    conversation_context: dict[str, Any] = Field(default_factory=dict)
    user_feedback: dict[str, Any] = Field(default_factory=dict)


class AnswerOutput(BaseModel):
    """业务回复节点输出。"""

    answer: str
    warnings: list[str] = Field(default_factory=list)
    render_type: str = "text"
    citations: list[dict[str, Any]] = Field(default_factory=list)


class QuestionRewriteOutput(BaseModel):
    """问题重写节点输出。"""

    rewritten_question: str
    need_user_input: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    image_profile_hint: str | None = None


class IntentRecognitionInput(BaseModel):
    """意图识别节点输入。"""

    rewritten_question: str
    conversation_context: dict[str, Any] = Field(default_factory=dict)
    user_feedback: dict[str, Any] = Field(default_factory=dict)


class ImageProfileOutput(BaseModel):
    """图像刻画节点输出。"""

    profile: str
    chart_candidates: list[str] = Field(default_factory=list)


class IntentRecognitionOutput(BaseModel):
    """意图识别节点输出。

    这里只表达自然语言层面的查询意图和检索线索，不确认 Headless 资产 ID。
    """

    intent_type: str
    confidence: float = Field(ge=0, le=1)
    metric_mentions: list[str] = Field(default_factory=list)
    dimension_mentions: list[str] = Field(default_factory=list)
    dimension_slots: list[dict[str, Any]] = Field(default_factory=list)
    time_mentions: list[str] = Field(default_factory=list)
    time_range: dict[str, Any] = Field(default_factory=dict)
    filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    required_slot_types: list[str] = Field(default_factory=list)
    query_shape: dict[str, Any] = Field(default_factory=dict)
    subject_domain: dict[str, Any] = Field(default_factory=dict)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class IntentValidationOutput(BaseModel):
    """意图语义校验节点输出。"""

    status: Literal["valid", "invalid"]
    reason_code: str
    repair_hint: str | None = None
    retryable: bool = False
    retry_count: int = Field(default=0, ge=0)
    max_retry_count: int = Field(default=2, ge=1)
    violations: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeRetrieveInput(BaseModel):
    """知识检索节点输入。"""

    rewritten_question: str
    intent: dict[str, Any]
    tenant_id: int
    dataset_id: int


class KnowledgeRetrieveOutput(BaseModel):
    """知识检索节点输出。"""

    hit: bool
    status: Literal["hit", "missed", "metric_ambiguous", "cross_model"]
    dataset_id: int | None = None
    schema_version: int | None = None
    index_version: int | None = None
    tables: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    candidate_groups: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    selected_assets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    slot_bindings: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    subject_domain: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    multi_query_plans: list[dict[str, Any]] = Field(default_factory=list)


class InteractionAskInput(BaseModel):
    """人机交互节点输入。"""

    clarification_type: str
    prompt: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    allowed_update_paths: list[str] = Field(default_factory=list)


class QueryPlanOutput(BaseModel):
    """语义查询计划：SQL 生成的唯一事实源。

    由 bind_query_plan 节点产出；检索证据、意图槽位、用户选择在此收敛。
    metrics/group_bys/filters/order 沿用编译槽位结构
    （asset_type/asset_id/display_name/operator/value）。
    """

    status: Literal["ready", "infeasible"]
    strategy: str = "semantic_compiler"
    select_mode: str = "aggregate"
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    group_bys: list[dict[str, Any]] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    having: list[dict[str, Any]] = Field(default_factory=list)
    time: dict[str, Any] = Field(default_factory=dict)
    order: list[dict[str, Any]] = Field(default_factory=list)
    limit: int | None = None
    sub_plans: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    infeasible_reason: str | None = None


class SqlGenerateInput(BaseModel):
    """SQL 生成节点输入。"""

    rewritten_question: str
    intent: dict[str, Any]
    knowledge: dict[str, Any]
    dataset_id: int


class SqlGenerateOutput(BaseModel):
    """SQL 生成节点输出。"""

    sql: str
    strategy: str = "placeholder"
    datasource_id: int | None = None
    explanation: str | None = None
    used_assets: list[dict[str, Any]] = Field(default_factory=list)


class SplitSqlGenerateOutput(BaseModel):
    """跨模型 SQL 生成节点输出。"""

    queries: list[dict[str, Any]] = Field(default_factory=list)
    strategy: str = "placeholder"
    explanation: str | None = None


class SqlExecuteInput(BaseModel):
    """SQL 执行节点输入。"""

    sql: str
    datasource_id: int
    permission: dict[str, Any] = Field(default_factory=dict)


class SqlExecuteOutput(BaseModel):
    """SQL 执行节点输出。"""

    status: Literal["succeeded", "failed"]
    queries: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    fields: list[str] = Field(default_factory=list)
    execution_ms: int = Field(default=0, ge=0)
    sampled_row_count: int = Field(default=0, ge=0)
    result_truncated: bool = False
    artifact_ref: dict[str, Any] | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None


class ResultValidationOutput(BaseModel):
    """执行结果校验节点输出。"""

    status: Literal["passed", "empty", "failed", "suspicious"]
    issues: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class SqlErrorInput(BaseModel):
    """SQL 异常处理节点输入。"""

    sql: str | None = None
    execution: dict[str, Any]


class SqlErrorOutput(BaseModel):
    """SQL 异常处理节点输出。"""

    error_code: str
    message: str
    retryable: bool = False
    repair_hint: str | None = None
    repair_plan: dict[str, Any] = Field(default_factory=dict)


class AnswerGenerateInput(BaseModel):
    """答案生成节点输入。"""

    question: str
    variables: dict[str, Any] = Field(default_factory=dict)


class RecommendationOutput(BaseModel):
    """问题推荐节点输出。"""

    questions: list[str] = Field(default_factory=list)


class FinalReplyInput(BaseModel):
    """最终回复节点输入。"""

    answer: dict[str, Any] = Field(default_factory=dict)
    recommendations: dict[str, Any] = Field(default_factory=dict)
    chart: dict[str, Any] = Field(default_factory=dict)


class FinalReplyOutput(BaseModel):
    """最终回复节点输出。"""

    final_answer: str
    recommendations: list[str] = Field(default_factory=list)
    chart: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


CHATBI_V1_OUTPUT_MODELS = {
    "question.classify": QuestionClassificationOutput,
    "answer.reject": AnswerOutput,
    "answer.chitchat": AnswerOutput,
    "question.rewrite": QuestionRewriteOutput,
    "question.draw_image_profile": ImageProfileOutput,
    "intent.recognize": IntentRecognitionOutput,
    "knowledge.retrieve": KnowledgeRetrieveOutput,
    "plan.bind": QueryPlanOutput,
    "sql.generate": SqlGenerateOutput,
    "sql.generate_split": SplitSqlGenerateOutput,
    "sql.execute": SqlExecuteOutput,
    "sql.execute_split": SqlExecuteOutput,
    "execution.validate": ResultValidationOutput,
    "sql.handle_error": SqlErrorOutput,
    "answer.generate": AnswerOutput,
    "question.recommend": RecommendationOutput,
    "answer.compose": FinalReplyOutput,
}
