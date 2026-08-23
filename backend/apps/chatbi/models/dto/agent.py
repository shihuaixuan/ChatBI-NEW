"""Agent 问数入口与运行配置契约。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from apps.chatbi.models.dto.research_agent import ResearchExecutionMode


class AgentQuestionRequest(BaseModel):
    chat_id: int = Field(gt=0)
    question: str = Field(min_length=1)
    datasource_id: int | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("question cannot be empty")
        return value


class AgentClarificationRequest(BaseModel):
    # 结构化选项回答或自由文本，二者至少其一。
    selections: list[dict[str, Any]] = Field(default_factory=list)
    text: str | None = None


class AgentStartStreamRequest(AgentQuestionRequest):
    """统一 SSE 入口的首次提问请求。"""

    action: Literal["start"] = "start"


class AgentResumeStreamRequest(BaseModel):
    """统一 SSE 入口的澄清恢复请求。"""

    action: Literal["resume"] = "resume"
    record_id: int = Field(gt=0)
    clarification: AgentClarificationRequest


AgentStreamRequest = Annotated[
    AgentStartStreamRequest | AgentResumeStreamRequest,
    Field(discriminator="action"),
]


class AgentConfig(BaseModel):
    enabled: bool = False
    datasource_allowlist: list[int] = Field(default_factory=list)
    max_steps: int = 12
    max_sql_retries: int = 2
    max_clarifications: int = 2
    repeat_fuse_threshold: int = 3
    timeout_seconds: int = 120
    token_budget: int = 100_000
    default_limit: int = 100
    sample_rows: int = 10
    summary_max_chars: int = 4000
    history_rounds: int = 3
    context_fold_chars: int = 30000
    # 默认只启用 P1 的确定性 FAST/PLAN 管道，避免新请求回退到 ReAct。
    execution_modes: tuple[str, ...] = ("fast", "plan")
    # Research 新旧执行路径的单一配置入口；阶段 1默认保持旧路径。
    research_execution_mode: ResearchExecutionMode = ResearchExecutionMode.LEGACY
    # 阶段 7 切流配置：shadow 双跑按数据集/租户白名单和确定性采样比例生效，
    # 用户可见路径始终是 legacy。阶段 7.5 起 agent 配置即全量切流（主路径
    # 换为新契约引擎，不参与采样）；回退即把 research_execution_mode 改回 legacy。
    research_shadow_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    research_shadow_dataset_allowlist: tuple[int, ...] = ()
    research_shadow_tenant_allowlist: tuple[int, ...] = ()
    plan_max_query_tasks: int = Field(default=5, gt=0)
    plan_query_concurrency: int = Field(default=4, gt=0)
    research_max_iterations: int = Field(default=6, gt=0, le=20)
    research_max_queries: int = Field(default=8, gt=0, le=50)
    research_max_model_calls: int = Field(default=8, gt=1, le=50)
    research_max_actions_per_iteration: int = Field(default=3, gt=0, le=10)
    # Harness 连续无新方向轮数的服务端停止阈值（§9.3.5.7）。
    research_max_stall_turns: int = Field(default=3, gt=0, le=20)
    research_max_duration_seconds: int = Field(default=300, gt=0, le=1800)
    research_max_evidence_rows: int = Field(default=20, gt=0, le=100)
    research_max_evidence_chars: int = Field(default=12_000, gt=0, le=100_000)
    compute_enabled: bool = True
    answer_citation_enforced: bool = True
    assisted_fallback_enabled: bool = False
    tool_timeout_seconds: float = 60.0
    tool_default_timeout_seconds: float = 30.0
    query_transient_retries: int = 1
    tool_parallel_workers: int = 4
