"""Agent 问数入口与运行配置契约。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


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
    execution_modes: tuple[str, ...] = ("react_legacy",)
    plan_max_query_tasks: int = 5
    compute_enabled: bool = True
    answer_citation_enforced: bool = True
    assisted_fallback_enabled: bool = False
    tool_timeout_seconds: float = 60.0
    tool_default_timeout_seconds: float = 30.0
    query_transient_retries: int = 1
    tool_parallel_workers: int = 4
