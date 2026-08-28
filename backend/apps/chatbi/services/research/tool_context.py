"""Research Agent 工具执行期间使用的服务端事实上下文。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from apps.chatbi.models.dto.analysis_evidence import (
    AnalysisEvidence,
    AnalysisEvidenceColumn,
    AnalysisEvidenceDependency,
    AnalysisEvidenceLevel,
    AnalysisEvidenceStatistics,
    AnalysisEvidenceVersion,
)
from apps.chatbi.models.dto.analysis_plan import ResultSetRef
from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    BudgetUsage,
    ClarificationRequest,
    ClarificationResponse,
    Completion,
    ConversationMessage,
    Evidence,
    EvidenceColumn,
    Finding,
    ResearchActionType,
    ResearchAgentInput,
    ResearchAgentRequirement,
    ResearchBudget,
    ResearchEvidence,
    ResearchEvidenceDependency,
    ResearchEvidenceStatistics,
    ResearchLogicalColumn,
    ResearchResultRef,
    ResearchState,
    ResearchStateStatus,
    ResearchToolCallRef,
    ResearchTurnDecision,
    TodoItem,
)
from apps.chatbi.services.evidence import EvidenceRegistry
from apps.chatbi.services.research.ports import ResearchToolCostEstimate
from apps.chatbi.services.research.state_changes import (
    apply_finding_changes,
    apply_todo_changes,
    replay_state_events,
    validate_event_sequence,
)
from apps.chatbi.services.research.state_snapshot import validate_evidence_dag
from apps.chatbi.services.research.tool_result_persistence import (
    serialize_research_tool_result,
)

if TYPE_CHECKING:
    from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
    from apps.chatbi.services.computation.engine import ComputeEngine
    from apps.chatbi.services.execution.result_store import ResultStore
    from apps.chatbi.services.research.semantic_runtime import SemanticQueryRuntime
    from apps.tool.context import CancellationSignal

RESEARCH_STATE_KEY = "research_state"
_RESULT_SETS_KEY = "result_sets"
_RUN_ID_KEY = "research_run_id"
_AGENT_INPUT_SNAPSHOTS_KEY = "agent_input_snapshots"
_LogicalValueRole = Literal[
    "group_key",
    "value",
    "current",
    "previous",
    "difference",
    "growth_rate",
    "share",
    "contribution",
]


@dataclass
class ResearchToolContext:
    """Research 状态的唯一可变入口。"""

    context: AgentToolContext
    requirement: ResearchAgentRequirement
    semantic_runtime: SemanticQueryRuntime | None = None
    semantic_retrieval_service: Any = None
    compute_engine: ComputeEngine | None = None
    agent_input: ResearchAgentInput | None = None
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    cancellation: CancellationSignal | None = None
    trace_recorder: Any = None
    failure_injector: Callable[[str], None] | None = None

    @property
    def run_id(self) -> str:
        return self.requirement.run_id

    @property
    def workspace_id(self) -> int:
        return self.context.workspace_id

    @property
    def dataset_id(self) -> int | None:
        return self.context.dataset_id

    @property
    def session(self) -> Any:
        return self.context.session

    @property
    def result_store(self) -> ResultStore | None:
        return self.context.result_store

    @property
    def iteration(self) -> int:
        """返回独立于旧计划系统的 ReAct 轮次。"""

        return int(self._state().get("step_index") or 0)

    @property
    def evidence_iteration(self) -> int:
        """返回 Evidence DAG 的深度，便于内部查询适配器使用。"""

        return self._max_evidence_depth()

    def next_step_index(self) -> int:
        current = int(self._state().get("step_index") or 0) + 1
        self._state()["step_index"] = current
        return current

    @property
    def finished(self) -> bool:
        return self.react_completion() is not None

    @property
    def completion(self) -> Completion | None:
        return self.react_completion()

    @property
    def current_status(self) -> ResearchStateStatus:
        raw = self._state().get("current_status")
        if raw is None:
            return ResearchStateStatus.RUNNING
        try:
            return ResearchStateStatus(raw)
        except ValueError as exc:
            raise ValueError("RESEARCH_AGENT_STATE_STATUS_INVALID") from exc

    def set_current_status(self, status: ResearchStateStatus | str) -> None:
        self._state()["current_status"] = ResearchStateStatus(status).value

    # ------------------------------------------------------------------ #
    # ResearchAgentInput
    # ------------------------------------------------------------------ #

    def current_agent_input(self) -> ResearchAgentInput:
        if self.agent_input is not None:
            return self.agent_input
        state = self._state()
        raw = state.get("agent_input")
        if not isinstance(raw, dict):
            ref = state.get("agent_input_ref")
            snapshots = state.get(_AGENT_INPUT_SNAPSHOTS_KEY)
            if isinstance(ref, str) and isinstance(snapshots, dict):
                raw = snapshots.get(ref)
        if not isinstance(raw, dict):
            raw = self.context.state.get("research_agent_input")
        if not isinstance(raw, dict):
            raise ValueError("RESEARCH_AGENT_INPUT_SNAPSHOT_REQUIRED")
        self.agent_input = ResearchAgentInput.model_validate(raw)
        return self.agent_input

    def save_agent_input(self, agent_input: ResearchAgentInput) -> ResearchAgentInput:
        ref = agent_input.agent_input_ref or self.next_agent_input_ref()
        if agent_input.agent_input_ref != ref:
            agent_input = agent_input.model_copy(update={"agent_input_ref": ref})
        payload = agent_input.model_dump(mode="json")
        snapshots = self._state().setdefault(_AGENT_INPUT_SNAPSHOTS_KEY, {})
        if not isinstance(snapshots, dict):
            raise TypeError("RESEARCH_AGENT_INPUT_SNAPSHOTS_INVALID")
        existing = snapshots.get(ref)
        if isinstance(existing, dict) and existing != payload:
            raise ValueError("RESEARCH_AGENT_INPUT_SNAPSHOT_IMMUTABLE")
        snapshots[ref] = payload
        self._state()["agent_input"] = payload
        self._state()["agent_input_ref"] = ref
        self.agent_input = agent_input
        return agent_input

    def next_agent_input_ref(self) -> str:
        snapshots = self._state().get(_AGENT_INPUT_SNAPSHOTS_KEY)
        count = len(snapshots) if isinstance(snapshots, dict) else 0
        return f"agent_input_{count + 1:02d}"

    def record_semantic_context_delta(self, delta: Any) -> None:
        """保存补充语义上下文的受控增量。"""

        deltas = self._state().setdefault("semantic_context_deltas", [])
        if not isinstance(deltas, list):
            raise TypeError("RESEARCH_AGENT_SEMANTIC_CONTEXT_DELTAS_INVALID")
        deltas.append(delta.model_dump(mode="json"))

    # ------------------------------------------------------------------ #
    # 澄清和 Completion
    # ------------------------------------------------------------------ #

    def clarification_request(self) -> ClarificationRequest | None:
        raw = self._state().get("pending_clarification")
        return ClarificationRequest.model_validate(raw) if isinstance(raw, dict) else None

    def save_clarification_request(self, request: ClarificationRequest) -> None:
        payload = request.model_dump(mode="json")
        self._state()["pending_clarification"] = payload
        self._state()["clarification_request"] = payload
        self.set_current_status(ResearchStateStatus.WAITING_FOR_USER)

    def resume_clarification(
        self,
        response: ClarificationResponse | Mapping[str, Any],
    ) -> ResearchAgentInput:
        if not isinstance(response, ClarificationResponse):
            response = ClarificationResponse.model_validate(response)
        request = self.clarification_request()
        if request is None:
            for raw in self._state().get("clarification_responses", []):
                if (
                    isinstance(raw, dict)
                    and raw.get("clarification_request_id")
                    == response.clarification_request_id
                ):
                    if raw != response.model_dump(mode="json"):
                        raise ValueError("RESEARCH_AGENT_CLARIFICATION_RESPONSE_CONFLICT")
                    return self.current_agent_input()
            raise ValueError("RESEARCH_AGENT_CLARIFICATION_NOT_PENDING")
        if response.clarification_request_id != request.clarification_request_id:
            raise ValueError("RESEARCH_AGENT_CLARIFICATION_REQUEST_MISMATCH")
        allowed = {item.option_id for item in request.options}
        if not set(response.selected_option_ids) <= allowed:
            raise ValueError("RESEARCH_AGENT_CLARIFICATION_OPTION_NOT_FOUND")
        if response.free_text and not request.allow_free_text:
            raise ValueError("RESEARCH_AGENT_CLARIFICATION_FREE_TEXT_FORBIDDEN")
        selected = [
            item.label
            for item in request.options
            if item.option_id in set(response.selected_option_ids)
        ]
        if response.free_text and response.free_text.strip():
            selected.append(response.free_text.strip())
        if not selected:
            raise ValueError("RESEARCH_AGENT_CLARIFICATION_RESPONSE_EMPTY")
        current = self.current_agent_input()
        updated = current.model_copy(
            update={
                "agent_input_ref": self.next_agent_input_ref(),
                "conversation_context": (
                    *current.conversation_context,
                    ConversationMessage(role="user", content="；".join(selected)),
                ),
            }
        )
        self.save_agent_input(updated)
        response_payload = response.model_dump(mode="json")
        responses = self._state().setdefault("clarification_responses", [])
        if not isinstance(responses, list):
            raise TypeError("RESEARCH_AGENT_CLARIFICATION_RESPONSES_INVALID")
        responses.append(response_payload)
        self._state()["clarification_response"] = response_payload
        self._state()["pending_clarification"] = None
        self.set_current_status(ResearchStateStatus.RUNNING)
        return updated

    def react_completion(self) -> Completion | None:
        raw = self._state().get("react_completion")
        return Completion.model_validate(raw) if isinstance(raw, dict) else None

    def save_react_completion(self, completion: Completion) -> None:
        self._state()["react_completion"] = completion.model_dump(mode="json")
        self.set_current_status(
            {
                "complete": ResearchStateStatus.COMPLETED,
                "partial": ResearchStateStatus.PARTIAL,
                "unanswerable": ResearchStateStatus.UNANSWERABLE,
            }[completion.status]
        )

    # ------------------------------------------------------------------ #
    # Finding、Todo、Attempt 和预算
    # ------------------------------------------------------------------ #

    def react_findings(self) -> tuple[Finding, ...]:
        return tuple(
            Finding.model_validate(item)
            for item in self._state().get("findings", [])
            if isinstance(item, dict)
        )

    def react_todos(self) -> tuple[TodoItem, ...]:
        return tuple(
            TodoItem.model_validate(item)
            for item in self._state().get("todo_items", [])
            if isinstance(item, dict)
        )

    def react_attempts(self) -> tuple[AttemptSummary, ...]:
        return tuple(
            AttemptSummary.model_validate(item)
            for item in self._state().get("attempted_actions", [])
            if isinstance(item, dict)
        )

    def react_budget_usage(self) -> BudgetUsage:
        raw = self._state().get("react_budget_usage")
        return BudgetUsage.model_validate(raw) if isinstance(raw, dict) else BudgetUsage()

    def record_research_attempt(self, attempt: AttemptSummary) -> None:
        attempts = self._state().setdefault("attempted_actions", [])
        if not isinstance(attempts, list):
            raise TypeError("RESEARCH_AGENT_ATTEMPTS_INVALID")
        payload = attempt.model_dump(mode="json")
        for existing in attempts:
            if isinstance(existing, dict) and existing.get("attempt_id") == attempt.attempt_id:
                if existing != payload:
                    raise ValueError("RESEARCH_AGENT_ATTEMPT_IMMUTABLE")
                return
        attempts.append(payload)

    def consume_research_cost(self, cost: ResearchToolCostEstimate) -> BudgetUsage:
        usage = self.react_budget_usage()
        updated = usage.model_copy(
            update={
                "query_calls": usage.query_calls + cost.query_calls,
                "compute_calls": usage.compute_calls + cost.compute_calls,
                "semantic_search_calls": usage.semantic_search_calls
                + cost.semantic_search_calls,
                "wall_time_ms": usage.wall_time_ms + cost.wall_time_ms,
                "query_cost": usage.query_cost + cost.query_cost,
            }
        )
        self._state()["react_budget_usage"] = updated.model_dump(mode="json")
        return updated

    def consume_react_model_turn(self, count: int = 1) -> BudgetUsage:
        if count < 0:
            raise ValueError("RESEARCH_AGENT_MODEL_TURN_COUNT_INVALID")
        usage = self.react_budget_usage()
        updated = usage.model_copy(update={"model_turns": usage.model_turns + count})
        self._state()["react_budget_usage"] = updated.model_dump(mode="json")
        return updated

    # ------------------------------------------------------------------ #
    # 状态变更和快照
    # ------------------------------------------------------------------ #

    def research_state(self) -> ResearchState:
        agent_input = self.current_agent_input()
        ref = agent_input.agent_input_ref or self._state().get("agent_input_ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("RESEARCH_AGENT_INPUT_REF_REQUIRED")
        return ResearchState(
            agent_input_ref=ref,
            evidence_refs=tuple(self._research_evidence_map()),
            findings=self.react_findings(),
            todo_items=self.react_todos(),
            attempted_actions=self.react_attempts(),
            budget_usage=self.react_budget_usage(),
            current_status=self.current_status,
            completion=self.react_completion(),
        )

    def apply_research_turn_decision(
        self,
        decision: ResearchTurnDecision | Mapping[str, Any],
        *,
        visible_tools: Sequence[str | ResearchActionType] | None = None,
    ) -> ResearchState:
        if not isinstance(decision, ResearchTurnDecision):
            decision = ResearchTurnDecision.model_validate(decision)
        if self.current_status is not ResearchStateStatus.RUNNING:
            raise ValueError("RESEARCH_AGENT_RUN_NOT_RUNNING")
        if visible_tools is not None:
            visible = {
                item.value if isinstance(item, ResearchActionType) else str(item)
                for item in visible_tools
            }
            if any(item.action_type.value not in visible for item in decision.actions):
                raise ValueError("RESEARCH_AGENT_ACTION_NOT_VISIBLE")
        known = set(self._research_evidence_map())
        findings = apply_finding_changes(
            self.react_findings(), decision.finding_changes, evidence_ids=known
        )
        todos = apply_todo_changes(
            self.react_todos(), decision.todo_changes, evidence_ids=known
        )
        state = self._state()
        events = state.get("state_events", [])
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise ValueError("RESEARCH_AGENT_STATE_EVENTS_INVALID")
        validate_event_sequence(events)
        if state.get("last_event_sequence", 0) != len(events):
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_SEQUENCE_INVALID")
        replayed = replay_state_events(events, evidence_ids=known)
        if replayed != (self.react_findings(), self.react_todos()):
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_REPLAY_MISMATCH")
        sequence = len(events) + 1
        new_events: list[dict[str, Any]] = []
        for event_type, changes in (
            ("finding_change", decision.finding_changes),
            ("todo_change", decision.todo_changes),
        ):
            for change in changes:
                new_events.append(
                    {
                        "event_sequence": sequence,
                        "event_type": event_type,
                        "change": change.model_dump(mode="json"),
                    }
                )
                sequence += 1
        candidate = ResearchState(
            agent_input_ref=self.research_state().agent_input_ref,
            evidence_refs=tuple(self._research_evidence_map()),
            findings=findings,
            todo_items=todos,
            attempted_actions=self.react_attempts(),
            budget_usage=self.react_budget_usage(),
            current_status=self.current_status,
            completion=self.react_completion(),
        )
        state["findings"] = [item.model_dump(mode="json") for item in findings]
        state["todo_items"] = [item.model_dump(mode="json") for item in todos]
        state["state_events"] = [*events, *new_events]
        state["last_event_sequence"] = sequence - 1
        return candidate

    def replay_research_state_events(self) -> ResearchState:
        state = self._state()
        events = state.get("state_events", [])
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise ValueError("RESEARCH_AGENT_STATE_EVENTS_INVALID")
        validate_event_sequence(events)
        if state.get("last_event_sequence", 0) != len(events):
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_SEQUENCE_INVALID")
        findings, todos = replay_state_events(
            events, evidence_ids=set(self._research_evidence_map())
        )
        if findings != self.react_findings() or todos != self.react_todos():
            raise ValueError("RESEARCH_AGENT_STATE_EVENT_REPLAY_MISMATCH")
        return self.research_state()

    def bind_to_context(self, **_: Any) -> None:
        """写入冻结 Requirement 和输入快照。"""

        existing_run_id = self.context.state.get(_RUN_ID_KEY)
        if existing_run_id and existing_run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        self.context.state[_RUN_ID_KEY] = self.run_id
        state = self._state()
        if not state.get("requirement"):
            state["requirement"] = self.requirement.model_dump(mode="json")
        if not state.get("execution_id") and self.context.execution_id:
            state["execution_id"] = self.context.execution_id
        if state.get("dataset_id") is None and self.context.dataset_id is not None:
            state["dataset_id"] = self.context.dataset_id
        if self.agent_input is not None:
            self.save_agent_input(self.agent_input)
        elif isinstance(state.get("agent_input"), dict):
            self.agent_input = ResearchAgentInput.model_validate(state["agent_input"])
        state.setdefault("current_status", ResearchStateStatus.RUNNING.value)
        state.setdefault("last_event_sequence", 0)
        state.setdefault("state_events", [])

    # ------------------------------------------------------------------ #
    # 新 Evidence 台账
    # ------------------------------------------------------------------

    def research_evidence(self, evidence_id: str) -> Evidence | None:
        raw = self._research_evidence_map().get(evidence_id)
        return Evidence.model_validate(raw) if isinstance(raw, dict) else None

    def known_evidence_ids(self) -> tuple[str, ...]:
        return tuple(self._research_evidence_map())

    def research_evidence_result_id(self, evidence_id: str) -> str | None:
        raw = self._research_evidence_results().get(evidence_id)
        if not isinstance(raw, dict) or raw.get("run_id") != self.run_id:
            return None
        value = raw.get("result_id")
        return value if isinstance(value, str) and value else None

    def record_research_evidence(self, evidence: Evidence, *, result_id: str) -> None:
        if not result_id:
            raise ValueError("RESEARCH_AGENT_RESULT_ID_REQUIRED")
        for parent_id in evidence.parent_evidence_ids:
            if self.research_evidence(parent_id) is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
        candidate = [
            item
            for item in self._research_evidence_values()
            if item.evidence_id != evidence.evidence_id
        ]
        candidate.append(evidence)
        validate_evidence_dag(candidate)
        evidence_map = self._research_evidence_map()
        payload = evidence.model_dump(mode="json")
        existing = evidence_map.get(evidence.evidence_id)
        if isinstance(existing, dict) and existing != payload:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_IMMUTABLE")
        result_map = self._research_evidence_results()
        result_payload = {"run_id": self.run_id, "result_id": result_id}
        existing_result = result_map.get(evidence.evidence_id)
        if isinstance(existing_result, dict) and existing_result != result_payload:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_RESULT_IMMUTABLE")
        evidence_map[evidence.evidence_id] = payload
        result_map[evidence.evidence_id] = result_payload
        refs = self._state().setdefault("evidence_refs", [])
        if not isinstance(refs, list):
            raise TypeError("RESEARCH_AGENT_STATE_EVIDENCE_REFS_INVALID")
        if evidence.evidence_id not in refs:
            refs.append(evidence.evidence_id)
        self._register_analysis_evidence(evidence)

    def analysis_evidences(self) -> tuple[AnalysisEvidence, ...]:
        return EvidenceRegistry(self.context.state).evidences()

    # ------------------------------------------------------------------ #
    # ToolResult、指纹和取消边界
    # ------------------------------------------------------------------ #

    def research_tool_result(self, tool_call_id: str) -> dict[str, Any] | None:
        raw = self._research_tool_results().get(tool_call_id)
        return dict(raw) if isinstance(raw, dict) else None

    def research_tool_results(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(item)
            for item in self._research_tool_results().values()
            if isinstance(item, dict)
        )

    def record_research_tool_result(self, result: Any) -> None:
        payload = serialize_research_tool_result(result)
        results = self._research_tool_results()
        existing = results.get(result.tool_call_id)
        if isinstance(existing, dict) and existing != payload:
            raise ValueError("RESEARCH_AGENT_TOOL_RESULT_IMMUTABLE")
        if existing is None:
            results[result.tool_call_id] = payload
            if self.trace_recorder is not None:
                self.trace_recorder(result)
            self.inject_failure("after_tool_result_saved")

    def fingerprint_known(self, fingerprint: str) -> bool:
        return fingerprint in self._fingerprints()

    def remember_fingerprint(
        self,
        fingerprint: str,
        *,
        evidence_id: str | None = None,
        result_id: str | None = None,
    ) -> None:
        entry = {
            key: value
            for key, value in (("evidence_id", evidence_id), ("result_id", result_id))
            if value is not None
        }
        current = self._fingerprints().get(fingerprint)
        if isinstance(current, dict) and current != entry:
            raise ValueError("RESEARCH_AGENT_ACTION_FINGERPRINT_CONFLICT")
        self._fingerprints()[fingerprint] = entry

    def fingerprint_entry(self, fingerprint: str) -> dict[str, str]:
        raw = self._fingerprints().get(fingerprint)
        return dict(raw) if isinstance(raw, dict) else {}

    def running_tool_call_ids(self) -> tuple[str, ...]:
        raw = self._state().get("running_tool_call_ids", [])
        if isinstance(raw, (list, tuple, set)):
            return tuple(str(item) for item in raw)
        return ()

    def discard_pending_tool_results(self) -> None:
        self._state()["discard_pending_tool_results"] = True

    def pending_tool_results_discarded(self) -> bool:
        return self._state().get("discard_pending_tool_results") is True

    def inject_failure(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)

    def result_set_payload(self, result_set_id: str) -> dict[str, Any] | None:
        payload = self.context.state.get(_RESULT_SETS_KEY)
        if not isinstance(payload, dict):
            return None
        item = payload.get(result_set_id)
        return item if isinstance(item, dict) else None

    def merge_result_ref(self, ref: ResultSetRef) -> None:
        result_sets = self.context.state.setdefault(_RESULT_SETS_KEY, {})
        if not isinstance(result_sets, dict):
            raise TypeError("RESEARCH_AGENT_RESULT_SETS_INVALID")
        result_sets[ref.result_set_id] = ref.model_dump(mode="json")

    def execution_identity(self) -> tuple[str, int, int, int]:
        execution_id = self.context.execution_id
        chat_id = self.context.chat_id
        record_id = self.context.record_id
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or not isinstance(chat_id, int)
            or chat_id <= 0
            or not isinstance(record_id, int)
            or record_id <= 0
        ):
            raise ValueError("RESEARCH_TOOL_EXECUTION_IDENTITY_REQUIRED")
        return execution_id, chat_id, record_id, self.workspace_id

    # ------------------------------------------------------------------ #
    # 内部存储和语义查询适配
    # ------------------------------------------------------------------ #

    def _state(self) -> dict[str, Any]:
        state = self.context.state.setdefault(RESEARCH_STATE_KEY, {})
        if not isinstance(state, dict):
            raise TypeError("RESEARCH_TOOL_STATE_INVALID")
        return state

    def _research_evidence_map(self) -> dict[str, Any]:
        value = self._state().setdefault("react_evidence", {})
        if not isinstance(value, dict):
            raise TypeError("RESEARCH_REACT_EVIDENCE_INVALID")
        return value

    def _research_evidence_values(self) -> list[Evidence]:
        return [
            Evidence.model_validate(raw)
            for raw in self._research_evidence_map().values()
            if isinstance(raw, dict)
        ]

    def _research_evidence_results(self) -> dict[str, Any]:
        value = self._state().setdefault("react_evidence_results", {})
        if not isinstance(value, dict):
            raise TypeError("RESEARCH_REACT_EVIDENCE_RESULTS_INVALID")
        return value

    def _research_tool_results(self) -> dict[str, Any]:
        value = self._state().setdefault("tool_results", {})
        if not isinstance(value, dict):
            raise TypeError("RESEARCH_TOOL_RESULTS_INVALID")
        return value

    def _fingerprints(self) -> dict[str, Any]:
        value = self._state().setdefault("request_fingerprints", {})
        if not isinstance(value, dict):
            raise TypeError("RESEARCH_TOOL_FINGERPRINTS_INVALID")
        return value

    def _max_evidence_depth(self) -> int:
        by_id = {
            item.evidence_id: item for item in self._research_evidence_values()
        }
        memo: dict[str, int] = {}

        def depth(evidence_id: str, visiting: set[str]) -> int:
            if evidence_id in memo:
                return memo[evidence_id]
            if evidence_id in visiting:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_DEPENDENCY_CYCLE")
            visiting.add(evidence_id)
            item = by_id[evidence_id]
            value = (
                max((depth(parent, visiting) + 1 for parent in item.parent_evidence_ids), default=0)
            )
            visiting.remove(evidence_id)
            memo[evidence_id] = value
            return value

        return max((depth(item.evidence_id, set()) for item in by_id.values()), default=0)

    def evidence(self, evidence_id: str) -> ResearchEvidence | None:
        """按需生成语义查询内部适配对象，不把旧台账写入状态。"""

        evidence = self.research_evidence(evidence_id)
        if evidence is None:
            return None
        result_id = self.research_evidence_result_id(evidence_id)
        if result_id is None:
            return None
        return self._legacy_evidence_view(evidence, result_id)

    def evidences(self) -> list[ResearchEvidence]:
        return [
            item
            for evidence_id in self.known_evidence_ids()
            if (item := self.evidence(evidence_id)) is not None
        ]

    def _legacy_evidence_view(self, evidence: Evidence, result_id: str) -> ResearchEvidence:
        logical_columns = tuple(
            ResearchLogicalColumn(
                asset_ref=column.semantic_ref or f"METRIC:computed:{index}",
                value_role=_logical_value_role(column),
                result_field=column.name,
            )
            for index, column in enumerate(evidence.columns)
        )
        rows = tuple(
            {
                column.name: value
                for column, value in zip(evidence.columns, row, strict=True)
            }
            for row in evidence.data.rows
        )
        dependencies = tuple(
            ResearchEvidenceDependency(
                evidence_id=parent_id,
                run_id=self.run_id,
                source_iteration=self._evidence_depth(parent_id),
                relation="derived",
            )
            for parent_id in evidence.parent_evidence_ids
        )
        return ResearchEvidence(
            run_id=self.run_id,
            evidence_id=evidence.evidence_id,
            source_tool_call=ResearchToolCallRef(
                run_id=self.run_id,
                tool_call_id=evidence.evidence_id.removeprefix("evidence:"),
            ),
            result_ref=ResearchResultRef(run_id=self.run_id, result_id=result_id),
            iteration=self._evidence_depth(evidence.evidence_id),
            purpose=evidence.purpose,
            metric_refs=evidence.definition.metrics,
            dimension_refs=evidence.definition.dimensions,
            time_ranges=tuple(binding.role for binding in self.requirement.time_bindings),
            logical_columns=logical_columns,
            statistics=ResearchEvidenceStatistics(
                row_count=evidence.data.row_count,
                truncated=evidence.data.truncated,
            ),
            sample_rows=rows,
            dependencies=dependencies,
            version_snapshot=self.requirement.version_snapshot,
        )

    def _evidence_depth(self, evidence_id: str) -> int:
        by_id = {item.evidence_id: item for item in self._research_evidence_values()}
        memo: dict[str, int] = {}

        def visit(current: str) -> int:
            if current in memo:
                return memo[current]
            item = by_id.get(current)
            if item is None:
                raise ValueError("RESEARCH_AGENT_EVIDENCE_NOT_FOUND")
            value = max((visit(parent) + 1 for parent in item.parent_evidence_ids), default=0)
            memo[current] = value
            return value

        return visit(evidence_id)

    def _register_analysis_evidence(self, evidence: Evidence) -> None:
        """把新 Evidence 投影到 Fast/Plan 共用台账，主台账仍只保存新契约。"""

        result_id = self.research_evidence_result_id(evidence.evidence_id)
        if result_id is None:
            raise ValueError("RESEARCH_AGENT_RESULT_ID_REQUIRED")
        iteration = self._evidence_depth(evidence.evidence_id)
        dependency_items = tuple(
            AnalysisEvidenceDependency(
                evidence_id=parent_id,
                relation="derived",
                source_iteration=self._evidence_depth(parent_id),
            )
            for parent_id in evidence.parent_evidence_ids
        )
        columns = tuple(
            AnalysisEvidenceColumn(
                asset_ref=column.semantic_ref or f"ASSET:result:{index}",
                value_role=_logical_value_role(column),
                result_field=column.name,
            )
            for index, column in enumerate(evidence.columns)
        )
        analysis = AnalysisEvidence(
            run_id=self.context.execution_id or self.run_id,
            evidence_id=evidence.evidence_id,
            mode="research",
            plan_id="research-react",
            node_id=evidence.evidence_id.removeprefix("evidence:")[:128],
            source_tool_call_id=evidence.evidence_id,
            result_set_id=result_id,
            iteration=iteration,
            purpose=evidence.purpose[:1000],
            metric_refs=evidence.definition.metrics,
            dimension_refs=evidence.definition.dimensions,
            logical_columns=columns,
            statistics=AnalysisEvidenceStatistics(
                row_count=evidence.data.row_count,
                truncated=evidence.data.truncated,
            ),
            sample_rows=tuple(
                {
                    column.name: value
                    for column, value in zip(evidence.columns, row, strict=True)
                }
                for row in evidence.data.rows
            ),
            dependencies=dependency_items,
            evidence_level=AnalysisEvidenceLevel.GOVERNED,
            version_snapshot=AnalysisEvidenceVersion(
                schema_version=self.requirement.version_snapshot.schema_version,
                contract_version=self.requirement.version_snapshot.contract_version,
                schema_fingerprint=self.requirement.version_snapshot.schema_fingerprint,
                scope_fingerprint=self.requirement.version_snapshot.scope_fingerprint,
                permission_fingerprint=self.requirement.version_snapshot.permission_fingerprint,
            ),
        )
        EvidenceRegistry(self.context.state).register(analysis)


def _logical_value_role(column: EvidenceColumn) -> _LogicalValueRole:
    if column.role == "dimension":
        return "group_key"
    if column.role == "computed":
        suffix_roles: tuple[tuple[str, _LogicalValueRole], ...] = (
            ("_difference", "difference"),
            ("_growth_rate", "growth_rate"),
            ("_share", "share"),
            ("_contribution", "contribution"),
            ("_current", "current"),
            ("_previous", "previous"),
        )
        for suffix, role in suffix_roles:
            if column.name.endswith(suffix):
                return role
    return "value"


__all__ = ["RESEARCH_STATE_KEY", "ResearchToolContext"]
