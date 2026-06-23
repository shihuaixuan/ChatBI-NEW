from copy import deepcopy
from typing import Any

from sqlmodel import Session

from apps.agentic_chat.tools.sql_executor import SqlExecuteTool
from apps.chatbi_workflow.capabilities.adapters.answer import (
    AnswerAdapter,
    AnswerModelClient,
)
from apps.chatbi_workflow.capabilities.adapters.interaction import (
    InteractionAdapter,
)
from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    HeadlessKnowledgeAdapter,
)
from apps.chatbi_workflow.capabilities.adapters.question import (
    QuestionAdapter,
    QuestionClassificationModelClient,
)
from apps.chatbi_workflow.capabilities.adapters.sql import SqlAdapter
from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi_workflow.capabilities.real import RealChatBICapabilityGateway
from apps.chatbi_workflow.conditions.core import register_chatbi_conditions
from apps.chatbi_workflow.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
    register_chatbi_minimal_handlers,
)
from apps.chatbi_workflow.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.headless.service import HeadlessSchemaBuilder
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.interaction import InteractionRequest
from apps.workflow_engine.domain.run import WorkflowRun
from apps.workflow_engine.infrastructure.events.publisher import DatabaseEventPublisher
from apps.workflow_engine.infrastructure.persistence.interaction_manager import (
    DatabaseInteractionManager,
)
from apps.workflow_engine.infrastructure.persistence.node_execution_repository import (
    NodeExecutionRepository,
)
from apps.workflow_engine.infrastructure.persistence.run_repository import RunRepository
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.registry.definition_validator import DefinitionValidator
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.graph_runtime import GraphRuntime
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class ChatBIV1InteractionResponsePatcher:
    """把 v1 交互回答转换为后续节点可直接消费的业务上下文补丁。"""

    def __call__(
        self,
        run: WorkflowRun,
        interaction: InteractionRequest,
        response: dict[str, Any],
    ) -> ContextPatch | None:
        if interaction.node_name != "ask_metric_selection":
            return None
        if response.get("skipped") is True:
            return None
        selected_metric = response.get("metric") or response.get("metric_id") or response.get("asset_id")
        if selected_metric in (None, ""):
            return None
        knowledge = deepcopy(run.context.variables.get("knowledge", {}))
        candidate = self._find_metric_candidate(knowledge, selected_metric)
        metric_asset = self._metric_asset(candidate, selected_metric)
        metric_binding = {
            **metric_asset,
            "confidence": 1.0,
        }
        selected_assets = deepcopy(knowledge.get("selected_assets") or {})
        selected_assets["metrics"] = [metric_asset]
        slot_bindings = deepcopy(knowledge.get("slot_bindings") or {})
        slot_bindings["metrics"] = [metric_binding]

        knowledge.update(
            {
                "hit": True,
                "status": "hit",
                "metrics": [metric_asset["biz_name"]],
                "ambiguities": [],
                "selected_assets": selected_assets,
                "slot_bindings": slot_bindings,
                "decision": {
                    "status": "user_selected",
                    "strategy": "metric_selection",
                    "reason": "用户已确认指标",
                },
            }
        )
        return ContextPatch(set_values={"variables.knowledge": knowledge})

    def _find_metric_candidate(self, knowledge: dict[str, Any], selected_metric: Any) -> Any:
        selected_text = str(selected_metric)
        for ambiguity in knowledge.get("ambiguities", []) or []:
            if ambiguity.get("type") != "metric":
                continue
            for candidate in ambiguity.get("candidates", []) or []:
                if self._candidate_matches(candidate, selected_text):
                    return candidate
        return selected_metric

    def _candidate_matches(self, candidate: Any, selected_text: str) -> bool:
        if isinstance(candidate, dict):
            values = (
                candidate.get("asset_id"),
                candidate.get("id"),
                candidate.get("biz_name"),
                candidate.get("display_name"),
                candidate.get("name"),
                candidate.get("title"),
            )
            return any(str(value) == selected_text for value in values if value not in (None, ""))
        return str(candidate) == selected_text

    def _metric_asset(self, candidate: Any, selected_metric: Any) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            text = str(candidate)
            return {
                "asset_id": text,
                "biz_name": text,
                "display_name": text,
                "source": "user_selected",
            }
        display_name = (
            candidate.get("display_name")
            or candidate.get("name")
            or candidate.get("title")
            or candidate.get("biz_name")
            or str(selected_metric)
        )
        biz_name = candidate.get("biz_name") or str(candidate.get("asset_id") or selected_metric)
        asset_id = candidate.get("asset_id") or candidate.get("id") or biz_name
        return {
            "asset_id": asset_id,
            "biz_name": str(biz_name),
            "display_name": str(display_name),
            "source": "user_selected",
        }


def build_placeholder_chatbi_runtime(session: Session) -> GraphRuntime:
    """组装可同步执行的 ChatBI 最小图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_minimal_definition())

    run_store = RunRepository(session)
    events = DatabaseEventPublisher(session)
    return GraphRuntime(
        registry=registry,
        run_store=run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(run_store, events),
        lease=InMemoryRunLease(),
        node_execution_recorder=NodeExecutionRepository(session),
    )


def build_placeholder_chatbi_v1_runtime(session: Session) -> GraphRuntime:
    """组装可同步执行的 ChatBI v1 占位图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    return _build_chatbi_v1_runtime(session, gateway)


def build_real_chatbi_v1_runtime(
    session: Session,
    question_model_client: QuestionClassificationModelClient | None = None,
    answer_model_client: AnswerModelClient | None = None,
) -> GraphRuntime:
    """组装真实 classify_question + 其他占位能力回退的 ChatBI v1 运行时。"""

    gateway = RealChatBICapabilityGateway(
        question_adapter=QuestionAdapter(model_client=question_model_client),
        answer_adapter=AnswerAdapter(model_client=answer_model_client),
        knowledge_adapter=HeadlessKnowledgeAdapter(schema_builder=HeadlessSchemaBuilder(session)),
        interaction_adapter=InteractionAdapter(schema_builder=HeadlessSchemaBuilder(session)),
        sql_adapter=SqlAdapter(
            schema_builder=HeadlessSchemaBuilder(session),
            execute_tool=SqlExecuteTool(session),
        ),
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )
    return _build_chatbi_v1_runtime(session, gateway)


def _build_chatbi_v1_runtime(session: Session, gateway) -> GraphRuntime:
    """组装 ChatBI v1 图运行时。"""

    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())

    run_store = RunRepository(session)
    events = DatabaseEventPublisher(session)
    return GraphRuntime(
        registry=registry,
        run_store=run_store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(run_store, events),
        lease=InMemoryRunLease(),
        interaction_manager=DatabaseInteractionManager(session),
        interaction_response_patcher=ChatBIV1InteractionResponsePatcher(),
        node_execution_recorder=NodeExecutionRepository(session),
    )
