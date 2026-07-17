from collections.abc import Iterator

from sqlmodel import Session

from apps.agent import crud
from apps.agent.loop import AgentLoop
from apps.agent.schemas import (
    AgentConfig,
    AgentQuestionRequest,
    AgentStartStreamRequest,
)
from common.core.config import settings
from common.core.db import engine


class AgentNotEnabledError(RuntimeError):
    """Agent 问数未启用。"""


class AgentDatasourceNotAllowedError(ValueError):
    """当前数据源不在 Agent 允许范围内。"""


def get_agent_config() -> AgentConfig:
    allowlist = [
        int(item.strip())
        for item in (settings.CHAT_AGENT_DATASOURCE_ALLOWLIST or "").split(",")
        if item.strip().isdigit()
    ]
    return AgentConfig(
        enabled=settings.CHAT_AGENT_ENABLED,
        datasource_allowlist=allowlist,
        max_steps=settings.CHAT_AGENT_MAX_STEPS,
        max_sql_retries=settings.CHAT_AGENT_MAX_SQL_RETRIES,
        max_clarifications=settings.CHAT_AGENT_MAX_CLARIFICATIONS,
        timeout_seconds=settings.CHAT_AGENT_TIMEOUT_SECONDS,
        token_budget=settings.CHAT_AGENT_TOKEN_BUDGET,
        default_limit=settings.CHAT_AGENT_DEFAULT_LIMIT,
        history_rounds=settings.CHAT_AGENT_HISTORY_ROUNDS,
        context_fold_chars=settings.CHAT_AGENT_CONTEXT_FOLD_CHARS,
    )


def create_agent_start_stream(
    current_user,
    request: AgentStartStreamRequest,
) -> Iterator[str]:
    """创建 Agent 首次提问事件流，供 HTTP 与 MCP 入口复用。"""

    config = get_agent_config()
    if not config.enabled:
        raise AgentNotEnabledError("Agent ChatBI is not enabled")
    if (
        request.datasource_id
        and config.datasource_allowlist
        and request.datasource_id not in config.datasource_allowlist
    ):
        raise AgentDatasourceNotAllowedError("Datasource is not enabled for Agent ChatBI")

    def stream() -> Iterator[str]:
        # SSE 流自己持有 session，避免跨响应生命周期传递 ORM 对象。
        with Session(engine) as stream_session:
            record, run = crud.create_record_and_run(
                stream_session,
                current_user,
                AgentQuestionRequest(**request.model_dump(exclude={"action"})),
                config.model_dump(),
            )
            loop = AgentLoop(stream_session, current_user, config)
            yield from loop.run(run, record)

    return stream()
