"""ChatBI Agent 单次运行的内存状态。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apps.chatbi.models import ChatbiAgentRun
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.orchestration.agent.budget import ChatBIBudgetPolicy
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.tools.base import (
    AgentToolContext,
    AgentToolContextServices,
)
from apps.temporal import TemporalContext
from apps.tool import BudgetGuard, NeverCancelled
from apps.tool.context import CancellationSignal
from common.core.config import settings

SEMANTIC_CONTRACT_VERSION = "r0"
BINDING_CONTRACT_VERSION = "legacy"


@dataclass
class AgentRuntimeState:
    """统一携带 ReAct 循环及挂起恢复所需的可变状态。"""

    run: ChatbiAgentRun
    record: Any
    context: AgentToolContext
    messages: list[AgentMessage]
    budget: BudgetGuard
    cancellation: CancellationSignal = field(default_factory=NeverCancelled)
    chatbi_budget: ChatBIBudgetPolicy = field(default_factory=ChatBIBudgetPolicy)
    system: AgentMessage | None = None
    runtime_context: AgentMessage | None = None

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

    @property
    def temporal_context(self) -> TemporalContext:
        """读取 Run 创建时固化的时间上下文，缺失或损坏时明确终止。"""

        return TemporalContext.model_validate(self.run.temporal_context)

    def serialized_messages(self) -> list[dict[str, Any]]:
        """返回可写入 Run 快照的消息结构。"""

        return [message.model_dump(mode="json") for message in self.messages]

    def persistable_context(self) -> dict[str, Any]:
        """排除全量结果和临时卸载对象，返回可恢复的派生状态。"""

        snapshot = {
            key: value
            for key, value in self.context.state.items()
            if key not in {"full_data", "tool_offloads", "semantic_schema"}
        }
        # R0 将契约版本写入每次 Run 快照，便于按版本分组回放和定位失败。
        snapshot.setdefault(
            "semantic_contract_version",
            "r1" if settings.CHATBI_MENTION_CONTRACT_ENABLED else SEMANTIC_CONTRACT_VERSION,
        )
        snapshot.setdefault("binding_contract_version", BINDING_CONTRACT_VERSION)
        return snapshot

    def budget_snapshot(self) -> dict[str, Any]:
        """合并通用预算与 ChatBI 业务预算的持久化快照。"""

        return {
            **self.budget.snapshot(),
            **self.chatbi_budget.snapshot(),
        }


class AgentRuntimeStateFactory:
    """为首次执行和恢复流程创建结构一致的运行状态。"""

    def __init__(
        self,
        session: Any,
        user_id: int | None,
        config: AgentConfig,
        tool_services: AgentToolContextServices,
        cancellation_signal_factory: Callable[[int], CancellationSignal] | None = None,
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._config = config
        self._tool_services = tool_services
        self._cancellation_signal_factory = cancellation_signal_factory

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
            result_artifact_service=self._tool_services.result_artifact_service,
            result_store=self._tool_services.result_store,
            config=self._config,
            temporal_context=TemporalContext.model_validate(run.temporal_context),
            state={
                "question": record.question or "",
                "execution_mode": run.execution_mode or "react_legacy",
            },
        )
        budget = BudgetGuard(
            max_steps=self._config.max_steps,
            token_budget=self._config.token_budget,
            repeat_fuse_threshold=self._config.repeat_fuse_threshold,
            timeout_seconds=self._config.timeout_seconds,
        )
        chatbi_budget = ChatBIBudgetPolicy(
            max_sql_retries=self._config.max_sql_retries,
            max_clarifications=self._config.max_clarifications,
        )
        return AgentRuntimeState(
            run=run,
            record=record,
            context=context,
            messages=[AgentMessage.user(record.question or "")],
            budget=budget,
            cancellation=(
                self._cancellation_signal_factory(run.id)
                if self._cancellation_signal_factory is not None
                else NeverCancelled()
            ),
            chatbi_budget=chatbi_budget,
        )


__all__ = ["AgentRuntimeState", "AgentRuntimeStateFactory"]
