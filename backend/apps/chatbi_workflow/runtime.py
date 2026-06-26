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
        if interaction.node_name == "ask_slot_clarification":
            return self._patch_slot_clarification(run, response)
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
        intent = run.context.variables.get("intent") if isinstance(run.context.variables.get("intent"), dict) else {}
        selected_assets, slot_bindings = self._prune_dimensions_after_metric_selection(
            selected_assets,
            slot_bindings,
            intent,
        )

        knowledge.update(
            {
                "hit": True,
                "status": "hit",
                "metrics": [metric_asset["biz_name"]],
                "dimensions": [item.get("biz_name") for item in selected_assets.get("dimensions", []) if item.get("biz_name")],
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

    @classmethod
    def _prune_dimensions_after_metric_selection(
        cls,
        selected_assets: dict[str, Any],
        slot_bindings: dict[str, Any],
        intent: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if cls._should_keep_plain_dimensions(intent):
            return selected_assets, slot_bindings
        filter_dimension_ids = {
            item.get("asset_id")
            for item in cls._items(slot_bindings.get("filters"))
            if str(item.get("asset_type") or "DIMENSION").upper() == "DIMENSION" and item.get("asset_id") is not None
        }
        if not filter_dimension_ids:
            selected_assets["dimensions"] = []
            slot_bindings["dimensions"] = []
            return selected_assets, slot_bindings
        selected_assets["dimensions"] = [
            item
            for item in cls._items(selected_assets.get("dimensions"))
            if item.get("asset_id") in filter_dimension_ids
        ]
        slot_bindings["dimensions"] = [
            item
            for item in cls._items(slot_bindings.get("dimensions"))
            if item.get("asset_id") in filter_dimension_ids
        ]
        return selected_assets, slot_bindings

    @staticmethod
    def _should_keep_plain_dimensions(intent: dict[str, Any]) -> bool:
        intent_type = str(intent.get("intent_type") or "").lower()
        query_shape = intent.get("query_shape") if isinstance(intent.get("query_shape"), dict) else {}
        if bool(query_shape.get("needs_group_by")):
            return True
        if intent_type in {"trend_analysis", "ranking_analysis", "comparison_analysis", "detail_query", "share_analysis"}:
            return True
        dimension_slots = intent.get("dimension_slots")
        if isinstance(dimension_slots, list):
            return any(
                isinstance(slot, dict) and str(slot.get("role") or "").lower() == "group_by"
                for slot in dimension_slots
            )
        return False

    @staticmethod
    def _items(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    def _patch_slot_clarification(self, run: WorkflowRun, response: dict[str, Any]) -> ContextPatch | None:
        if response.get("skipped") is True:
            return None
        intent = deepcopy(run.context.variables.get("intent", {}))
        if not intent:
            return None

        if "subject_domain" in (intent.get("ambiguous_slots") or []):
            return self._patch_subject_domain(intent, response)

        dimension_name = self._dimension_name(intent)
        usage = response.get("dimension_usage")
        if usage == "ignore":
            self._remove_dimension(intent, dimension_name)
        elif usage == "group_by":
            self._set_dimension_slot(intent, dimension_name, role="group_by", value=None, value_status="not_provided")
        else:
            dimension_values = response.get("dimension_values")
            if isinstance(dimension_values, dict):
                updated = False
                for name, raw_value in dimension_values.items():
                    value = self._normalize_dimension_value(str(name), raw_value)
                    if value in (None, ""):
                        continue
                    self._set_dimension_slot(
                        intent,
                        str(name),
                        role="filter",
                        value=str(value),
                        value_status="provided",
                    )
                    updated = True
                if not updated:
                    return None
            else:
                dimension_value = response.get("dimension_value") or response.get("filter_value")
                dimension_value = self._normalize_dimension_value(dimension_name, dimension_value)
                if dimension_value in (None, "") and response.get("dimension") not in (None, "", dimension_name):
                    dimension_value = self._normalize_dimension_value(dimension_name, response.get("dimension"))
                if dimension_value not in (None, ""):
                    self._set_dimension_slot(
                        intent,
                        dimension_name,
                        role="filter",
                        value=str(dimension_value),
                        value_status="provided",
                    )
                else:
                    return None

        ambiguous_slots = [slot for slot in intent.get("ambiguous_slots", []) if slot != "dimension"]
        intent["ambiguous_slots"] = ambiguous_slots
        return ContextPatch(set_values={"variables.intent": intent})

    @staticmethod
    def _normalize_dimension_value(dimension_name: str, raw_value: Any) -> str | None:
        if raw_value in (None, ""):
            return None
        value = str(raw_value).strip()
        if not value:
            return None
        dimension = str(dimension_name or "").strip()
        if not dimension:
            return value
        for separator in ("为", "=", "是", ":", "："):
            prefix = f"{dimension}{separator}"
            if value.startswith(prefix):
                return value[len(prefix):].strip() or None
        return value

    def _patch_subject_domain(self, intent: dict[str, Any], response: dict[str, Any]) -> ContextPatch | None:
        domain_id = self._int_or_none(response.get("domain_id") or response.get("subject_domain_id"))
        if domain_id is None:
            return None
        domain_name = response.get("subject_domain") or response.get("domain_name") or str(domain_id)
        intent["subject_domain"] = {
            "status": "selected",
            "domain_id": domain_id,
            "domain_name": str(domain_name),
            "domain_biz_name": response.get("domain_biz_name"),
            "confidence": 1.0,
            "reason": "用户已确认主题域",
            "candidate_domain_ids": [domain_id],
        }
        intent["ambiguous_slots"] = [slot for slot in intent.get("ambiguous_slots", []) if slot != "subject_domain"]
        return ContextPatch(set_values={"variables.intent": intent})

    def _dimension_name(self, intent: dict[str, Any]) -> str:
        dimension_slots = intent.get("dimension_slots") if isinstance(intent.get("dimension_slots"), list) else []
        for slot in dimension_slots:
            if isinstance(slot, dict) and slot.get("name"):
                return str(slot["name"])
        dimension_mentions = intent.get("dimension_mentions") if isinstance(intent.get("dimension_mentions"), list) else []
        if dimension_mentions:
            return str(dimension_mentions[0])
        return str(intent.get("dimension") or "维度")

    def _set_dimension_slot(
        self,
        intent: dict[str, Any],
        dimension_name: str,
        role: str,
        value: Any,
        value_status: str,
    ) -> None:
        slot = {
            "name": dimension_name,
            "role": role,
            "value": value,
            "value_status": value_status,
        }
        dimension_slots = intent.get("dimension_slots")
        if not isinstance(dimension_slots, list):
            intent["dimension_slots"] = [slot]
            return
        for index, existing in enumerate(dimension_slots):
            if isinstance(existing, dict) and existing.get("name") == dimension_name:
                dimension_slots[index] = slot
                break
        else:
            dimension_slots.append(slot)
        intent["dimension_slots"] = dimension_slots
        dimension_mentions = intent.get("dimension_mentions")
        if isinstance(dimension_mentions, list) and dimension_name not in dimension_mentions:
            dimension_mentions.append(dimension_name)
        elif not isinstance(dimension_mentions, list):
            intent["dimension_mentions"] = [dimension_name]

    @staticmethod
    def _remove_dimension(intent: dict[str, Any], dimension_name: str) -> None:
        dimension_slots = intent.get("dimension_slots")
        if isinstance(dimension_slots, list):
            intent["dimension_slots"] = [
                slot for slot in dimension_slots if not (isinstance(slot, dict) and slot.get("name") == dimension_name)
            ]
        dimension_mentions = intent.get("dimension_mentions")
        if isinstance(dimension_mentions, list):
            intent["dimension_mentions"] = [dimension for dimension in dimension_mentions if dimension != dimension_name]

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

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


def build_placeholder_chatbi_runtime(session: Session, commit_events: bool = False) -> GraphRuntime:
    """组装可同步执行的 ChatBI 最小图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_minimal_definition())

    run_store = RunRepository(session)
    events = DatabaseEventPublisher(session, commit_on_publish=commit_events)
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


def build_placeholder_chatbi_v1_runtime(session: Session, commit_events: bool = False) -> GraphRuntime:
    """组装可同步执行的 ChatBI v1 占位图运行时。"""

    gateway = PlaceholderChatBICapabilityGateway()
    return _build_chatbi_v1_runtime(session, gateway, commit_events=commit_events)


def build_real_chatbi_v1_runtime(
    session: Session,
    question_model_client: QuestionClassificationModelClient | None = None,
    answer_model_client: AnswerModelClient | None = None,
    commit_events: bool = False,
) -> GraphRuntime:
    """组装真实 classify_question + 其他占位能力回退的 ChatBI v1 运行时。"""

    schema_builder = HeadlessSchemaBuilder(session)
    gateway = RealChatBICapabilityGateway(
        question_adapter=QuestionAdapter(model_client=question_model_client, schema_builder=schema_builder),
        answer_adapter=AnswerAdapter(model_client=answer_model_client),
        knowledge_adapter=HeadlessKnowledgeAdapter(schema_builder=schema_builder),
        interaction_adapter=InteractionAdapter(schema_builder=schema_builder),
        sql_adapter=SqlAdapter(
            schema_builder=schema_builder,
            execute_tool=SqlExecuteTool(session),
        ),
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )
    return _build_chatbi_v1_runtime(session, gateway, commit_events=commit_events)


def _build_chatbi_v1_runtime(session: Session, gateway, commit_events: bool = False) -> GraphRuntime:
    """组装 ChatBI v1 图运行时。"""

    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)

    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())

    run_store = RunRepository(session)
    events = DatabaseEventPublisher(session, commit_on_publish=commit_events)
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
