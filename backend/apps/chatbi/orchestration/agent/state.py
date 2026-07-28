"""ChatBI Agent 单次运行的内存状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.models import ChatbiAgentRun
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.tools.base import (
    AgentToolContext,
    AgentToolContextServices,
)
from apps.tool import BudgetGuard


@dataclass
class AgentRuntimeState:
    """统一携带 ReAct 循环及挂起恢复所需的可变状态。"""

    run: ChatbiAgentRun
    record: Any
    context: AgentToolContext
    messages: list[AgentMessage]
    budget: BudgetGuard
    system: AgentMessage | None = None

    def require_system(self) -> AgentMessage:
        """进入规划循环前必须已经构造系统消息。"""

        if self.system is None:
            raise RuntimeError("AGENT_SYSTEM_PROMPT_MISSING")
        return self.system

    def require_run_id(self) -> int:
        """需要发布事件或保存结果时，Run 必须已经持久化。"""

        if self.run.id is None:
            raise RuntimeError("AGENT_RUN_ID_MISSING")
        return self.run.id

    def serialized_messages(self) -> list[dict[str, Any]]:
        """返回可写入 Run 快照的消息结构。"""

        return [message.model_dump(mode="json") for message in self.messages]

    def persistable_context(self) -> dict[str, Any]:
        """排除全量结果和临时卸载对象，返回可恢复的派生状态。"""

        return {
            key: value
            for key, value in self.context.state.items()
            if key not in {"full_data", "tool_offloads"}
        }


class AgentRuntimeStateFactory:
    """为首次执行和恢复流程创建结构一致的运行状态。"""

    def __init__(
        self,
        session: Any,
        user_id: int | None,
        config: AgentConfig,
        tool_services: AgentToolContextServices,
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._config = config
        self._tool_services = tool_services

    def create(self, run: ChatbiAgentRun, record: Any) -> AgentRuntimeState:
        """创建状态，并统一校验 Run 与 ChatRecord 的执行归属。"""

        if run.id is None or record.id is None:
            raise RuntimeError("AGENT_EXECUTION_OWNERSHIP_MISSING")
        context = AgentToolContext(
            session=self._session,
            oid=run.oid,
            user_id=self._user_id,
            datasource_id=record.datasource,
            execution_id=f"agent:{run.id}",
            chat_id=run.chat_id,
            record_id=record.id,
            dataset_id=record.dataset_id,
            term_query_service=self._tool_services.term_query_service,
            query_service=self._tool_services.query_service,
            semantic_query_service=self._tool_services.semantic_query_service,
            semantic_retrieval_service=(
                self._tool_services.semantic_retrieval_service
            ),
            physical_schema_service=self._tool_services.physical_schema_service,
            result_artifact_service=self._tool_services.result_artifact_service,
            config=self._config,
            state={"question": record.question or ""},
        )
        budget = BudgetGuard(
            max_steps=self._config.max_steps,
            token_budget=self._config.token_budget,
            repeat_fuse_threshold=self._config.repeat_fuse_threshold,
            max_sql_retries=self._config.max_sql_retries,
            timeout_seconds=self._config.timeout_seconds,
            max_clarifications=self._config.max_clarifications,
        )
        return AgentRuntimeState(
            run=run,
            record=record,
            context=context,
            messages=[AgentMessage.user(record.question or "")],
            budget=budget,
        )


__all__ = ["AgentRuntimeState", "AgentRuntimeStateFactory"]
