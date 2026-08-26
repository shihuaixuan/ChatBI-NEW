"""Research Tool Context：阶段 3 工具层的服务端运行事实门面。

Context 包装一次 Research Run 的执行上下文（AgentToolContext），并把这些
可变研究事实集中存放在 ``state["research_state"]``：

- 证据台账（Evidence Registry）；
- 已完成的 ToolObservation（按 tool_call_id 重放，保证不重复写 ResultStore）；
- 查询/计算指纹（同内容请求去重，不重复消耗预算）；
- 预算用量；
- 当前迭代号；
- 终态 completion。

Context 只在服务端 Tool 执行时使用，不向模型序列化。所有值以 JSON dump
形式落盘，保证阶段 4 可以把它整体持久化为 derived_state。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from apps.chatbi.models.dto.analysis_evidence import AnalysisEvidence
from apps.chatbi.models.dto.analysis_plan import ResultSetRef
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchBudget,
    ResearchBudgetUsage,
    ResearchCompletion,
    ResearchEvidence,
    ResearchHypothesisAssessment,
    ResearchPlanAddition,
    SemanticAssessment,
    ToolObservation,
)
from apps.chatbi.services.evidence import (
    EvidenceRegistry,
    build_research_analysis_evidence,
)
from apps.chatbi.services.planning.execution_state import (
    PLAN_EXECUTION_STATE_KEY,
    PlanNodeExecutionStatus,
    UnifiedPlanExecutionState,
    UnifiedPlanNode,
    append_plan_nodes,
    build_plan_execution_state,
    load_plan_execution_state,
    transition_plan_node,
)
from apps.chatbi.services.research.hypothesis_evaluator import HypothesisAuditRecord
from apps.chatbi.services.research.state_snapshot import validate_evidence_dag

if TYPE_CHECKING:
    from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
    from apps.chatbi.services.computation.engine import ComputeEngine
    from apps.chatbi.services.execution.result_store import ResultStore
    from apps.chatbi.services.research.semantic_runtime import SemanticQueryRuntime
    from apps.tool.context import CancellationSignal

RESEARCH_STATE_KEY = "research_state"
_RESULT_SETS_KEY = "result_sets"
_RUN_ID_KEY = "research_run_id"


@dataclass
class ResearchToolContext:
    """工具执行期间读取和写入研究事实的唯一入口。"""

    context: AgentToolContext
    requirement: ResearchAgentRequirement
    semantic_runtime: SemanticQueryRuntime | None = None
    compute_engine: ComputeEngine | None = None
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    cancellation: CancellationSignal | None = None
    # Trace Recorder 由宿主注入；观察记录是唯一回调点。
    trace_recorder: Any = None

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
        """底层命名结果集存储；由宿主随执行上下文注入。"""

        return self.context.result_store

    @property
    def iteration(self) -> int:
        return int(self._state().get("iteration") or 0)

    @property
    def finished(self) -> bool:
        return self._state().get("completion") is not None

    @property
    def completion(self) -> ResearchCompletion | None:
        raw = self._state().get("completion")
        if raw is None:
            return None
        return ResearchCompletion.model_validate(raw)

    # ------------------------------------------------------------------ #
    # 状态初始化
    # ------------------------------------------------------------------ #

    def bind_to_context(self) -> None:
        """把 run 身份和冻结 Requirement 写入底层 state。

        Requirement 以 JSON 形式随 research_state 一起持久化，恢复时只使用
        这份冻结快照，不重新解析当前发布的语义 Schema。
        """

        state = self.context.state
        if not state.get(_RUN_ID_KEY):
            state[_RUN_ID_KEY] = self.run_id
        research = self._state()
        if not research.get("requirement"):
            research["requirement"] = self.requirement.model_dump(mode="json")
        if not research.get("execution_id") and self.context.execution_id:
            research["execution_id"] = self.context.execution_id
        if research.get("dataset_id") is None and self.context.dataset_id is not None:
            research["dataset_id"] = self.context.dataset_id
        if not isinstance(state.get(PLAN_EXECUTION_STATE_KEY), dict):
            state[PLAN_EXECUTION_STATE_KEY] = build_plan_execution_state(
                f"research-{self.run_id}"
            ).model_dump(mode="json")
        # 恢复旧快照时把 Research Evidence 投影到统一台账；原 Research 台账
        # 继续保留，避免破坏当前完成度和报告恢复协议。
        existing = tuple(
            ResearchEvidence.model_validate(raw)
            for raw in self._evidence_map().values()
            if isinstance(raw, dict)
        )
        if existing:
            EvidenceRegistry(self.context.state).merge_missing(
                tuple(
                    build_research_analysis_evidence(
                        item,
                        run_id=self._analysis_evidence_run_id(),
                    )
                    for item in existing
                )
            )

    # ------------------------------------------------------------------ #
    # 证据台账
    # ------------------------------------------------------------------ #

    def evidence(self, evidence_id: str) -> ResearchEvidence | None:
        raw = self._evidence_map().get(evidence_id)
        if raw is None:
            return None
        return ResearchEvidence.model_validate(raw)

    def evidences(self) -> list[ResearchEvidence]:
        return [
            ResearchEvidence.model_validate(raw)
            for raw in self._evidence_map().values()
        ]

    def analysis_evidences(self) -> tuple[AnalysisEvidence, ...]:
        """读取三种分析模式共用的 Evidence 台账。"""

        return EvidenceRegistry(self.context.state).evidences()

    def known_evidence_ids(self) -> tuple[str, ...]:
        return tuple(self._evidence_map())

    def register_evidence(self, evidence: ResearchEvidence) -> None:
        """登记当前 Run 的证据；跨 Run 登记直接拒绝，登记前校验全图无环。

        同 ID 重复登记按“后写覆盖”处理（服务端补齐列的合法路径），
        但覆盖后的全图仍必须通过 DAG 校验。
        """

        if evidence.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        if evidence.version_snapshot != self.requirement.version_snapshot:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_VERSION_MISMATCH")
        candidate = [
            item
            for item in self.evidences()
            if item.evidence_id != evidence.evidence_id
        ]
        candidate.append(evidence)
        validate_evidence_dag(candidate)
        self._evidence_map()[evidence.evidence_id] = evidence.model_dump(mode="json")
        EvidenceRegistry(self.context.state).register(
            build_research_analysis_evidence(
                evidence,
                run_id=self._analysis_evidence_run_id(),
            )
        )
        # 语义评估只对提交时的 Evidence 集合有效；新增证据后必须重新判断。
        self._state().pop("semantic_assessment", None)

    def _analysis_evidence_run_id(self) -> str:
        """统一 Evidence 使用 Agent 执行 ID，模式信息只保存在 mode 字段。"""

        value = self.context.execution_id
        if not isinstance(value, str) or not value:
            raise ValueError("RESEARCH_ANALYSIS_EVIDENCE_EXECUTION_ID_REQUIRED")
        return value

    # ------------------------------------------------------------------ #
    # Observation 重放
    # ------------------------------------------------------------------ #

    def observation(self, tool_call_id: str) -> ToolObservation | None:
        raw = self._observations().get(tool_call_id)
        if raw is None:
            return None
        return ToolObservation.model_validate(raw)

    def observations(self) -> list[ToolObservation]:
        """按记录顺序返回全部终态观察（含成功与失败）。"""

        return [
            ToolObservation.model_validate(raw) for raw in self._observations().values()
        ]

    def record_observation(self, observation: ToolObservation) -> None:
        if observation.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_EVIDENCE_CROSS_RUN")
        self._observations()[observation.tool_call_id] = observation.model_dump(
            mode="json"
        )
        if self.trace_recorder is not None:
            self.trace_recorder(observation)

    # ------------------------------------------------------------------ #
    # 请求指纹去重
    # ------------------------------------------------------------------ #

    def fingerprint_known(self, fingerprint: str) -> bool:
        return fingerprint in self._fingerprints()

    def remember_fingerprint(
        self,
        fingerprint: str,
        *,
        evidence_id: str | None = None,
        result_id: str | None = None,
    ) -> None:
        entry: dict[str, str] = {}
        if evidence_id is not None:
            entry["evidence_id"] = evidence_id
        if result_id is not None:
            entry["result_id"] = result_id
        self._fingerprints()[fingerprint] = entry

    def fingerprint_entry(self, fingerprint: str) -> dict[str, str]:
        return dict(self._fingerprints().get(fingerprint) or {})

    # ------------------------------------------------------------------ #
    # 预算
    # ------------------------------------------------------------------ #

    def budget_usage(self) -> ResearchBudgetUsage:
        raw = self._state().get("budget_usage")
        if not isinstance(raw, dict):
            return ResearchBudgetUsage()
        return ResearchBudgetUsage.model_validate(raw)

    def consume_query(self, count: int = 1) -> ResearchBudgetUsage:
        usage = self.budget_usage()
        updated = ResearchBudgetUsage(
            queries=usage.queries + count,
            model_calls=usage.model_calls,
            duration_seconds=usage.duration_seconds,
            evidence_rows=usage.evidence_rows,
            evidence_chars=usage.evidence_chars,
        )
        self._state()["budget_usage"] = updated.model_dump(mode="json")
        return updated

    def consume_model_call(self, count: int = 1) -> ResearchBudgetUsage:
        """Harness 每轮推理后登记模型调用；快照的剩余预算据此推导。"""

        usage = self.budget_usage()
        updated = ResearchBudgetUsage(
            queries=usage.queries,
            model_calls=usage.model_calls + count,
            duration_seconds=usage.duration_seconds,
            evidence_rows=usage.evidence_rows,
            evidence_chars=usage.evidence_chars,
        )
        self._state()["budget_usage"] = updated.model_dump(mode="json")
        return updated

    # ------------------------------------------------------------------ #
    # 迭代推进与终态
    # ------------------------------------------------------------------ #

    def advance_iteration(self, minimum: int = 0) -> int:
        """把当前迭代推进到至少 ``minimum``，返回派生证据应使用的迭代号。"""

        target = max(self.iteration, minimum)
        self._state()["iteration"] = target
        return target

    def finish(self, completion: ResearchCompletion) -> None:
        if completion.run_id != self.run_id:
            raise ValueError("RESEARCH_AGENT_COMPLETION_CROSS_RUN")
        self._state()["completion"] = completion.model_dump(mode="json")

    def semantic_assessment(self) -> SemanticAssessment | None:
        """读取对当前 Evidence 集合仍然有效的模型评估。"""

        raw = self._state().get("semantic_assessment")
        if not isinstance(raw, dict):
            return None
        if raw.get("evidence_ids") != sorted(self.known_evidence_ids()):
            return None
        payload = raw.get("assessment")
        if not isinstance(payload, dict):
            raise TypeError("RESEARCH_SEMANTIC_ASSESSMENT_INVALID")
        return SemanticAssessment.model_validate(payload)

    def set_semantic_assessment(self, assessment: SemanticAssessment) -> None:
        """保存模型评估，并将计划增量直接写入规范 DAG。"""

        if assessment.proposed_plan_additions:
            self.append_plan_additions(assessment.proposed_plan_additions)
        self._state()["semantic_assessment"] = {
            "evidence_ids": sorted(self.known_evidence_ids()),
            "assessment": assessment.model_dump(mode="json"),
        }

    def append_plan_additions(
        self,
        additions: Sequence[ResearchPlanAddition],
    ) -> None:
        """把模型生成的首次计划或后续增量追加到同一个规范 DAG。"""

        plan_state = self.plan_execution_state()
        known_node_ids = {node.id for node in plan_state.nodes}
        addition_ids = {item.addition_id for item in additions}
        nodes: list[UnifiedPlanNode] = []
        for item in additions:
            dependencies = item.dependency_node_ids
            if not set(dependencies) <= known_node_ids | addition_ids:
                raise ValueError("RESEARCH_PLAN_DEPENDENCY_UNKNOWN")
            task_type: Literal["query", "compute", "inspect"]
            if item.tool_name == "query_semantic_data":
                task_type = "query"
            elif item.tool_name == "compute_evidence":
                task_type = "compute"
            else:
                task_type = "inspect"
            nodes.append(
                UnifiedPlanNode(
                    id=item.addition_id,
                    description=(
                        item.description
                        or str(item.arguments.get("purpose") or item.addition_id)
                    ),
                    task_type=task_type,
                    dependencies=dependencies,
                    tool_name=item.tool_name,
                    gap_id=item.gap_id,
                    arguments=dict(item.arguments),
                    output_type=item.output_type,
                    expected_output=(
                        item.expected_output
                        or {
                            "query": "返回受治理查询 Evidence",
                            "compute": "返回确定性计算 Evidence",
                            "inspect": "返回 Evidence 检查结果",
                        }[task_type]
                    ),
                )
            )
        self._save_plan_execution_state(
            append_plan_nodes(plan_state, nodes)
        )

    # ------------------------------------------------------------------ #
    # 统一计划执行状态
    # ------------------------------------------------------------------ #

    def plan_execution_state(self) -> UnifiedPlanExecutionState:
        """读取 Research 与其他模式共用的规范计划状态。"""

        raw = self.context.state.get(PLAN_EXECUTION_STATE_KEY)
        if not isinstance(raw, dict):
            raise TypeError("RESEARCH_PLAN_EXECUTION_STATE_REQUIRED")
        loaded = load_plan_execution_state(raw)
        if loaded is None:
            raise TypeError("RESEARCH_PLAN_EXECUTION_STATE_REQUIRED")
        return loaded

    def mark_plan_node(
        self,
        node_id: str,
        *,
        status: PlanNodeExecutionStatus,
        tool_call_id: str | None = None,
        evidence_ids: Sequence[str] = (),
        error_code: str | None = None,
    ) -> None:
        self._save_plan_execution_state(
            transition_plan_node(
                self.plan_execution_state(),
                node_id,
                status,
                tool_call_id=tool_call_id,
                evidence_ids=evidence_ids,
                error_code=error_code,
            )
        )

    def ready_plan_nodes(self) -> tuple[UnifiedPlanNode, ...]:
        """返回依赖全部成功的待执行节点，并标记依赖失败节点。"""

        plan = self.plan_execution_state()
        node_by_id = {node.id: node for node in plan.nodes}
        changed = plan
        ready: list[UnifiedPlanNode] = []
        for node in plan.nodes:
            task_state = changed.task_states[node.id]
            if task_state.status is not PlanNodeExecutionStatus.PENDING:
                continue
            dependency_states = [
                changed.task_states[dependency].status
                for dependency in node.dependencies
            ]
            if any(
                status
                in {
                    PlanNodeExecutionStatus.FAILED,
                    PlanNodeExecutionStatus.SKIPPED_DEPENDENCY,
                    PlanNodeExecutionStatus.CANCELLED,
                }
                for status in dependency_states
            ):
                changed = transition_plan_node(
                    changed,
                    node.id,
                    PlanNodeExecutionStatus.SKIPPED_DEPENDENCY,
                    error_code="PLAN_DEPENDENCY_FAILED",
                )
            elif all(
                status is PlanNodeExecutionStatus.SUCCEEDED
                for status in dependency_states
            ):
                ready.append(node_by_id[node.id])
        if changed is not plan:
            self._save_plan_execution_state(changed)
        return tuple(ready)

    def _save_plan_execution_state(self, state: UnifiedPlanExecutionState) -> None:
        self.context.state[PLAN_EXECUTION_STATE_KEY] = state.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # 前提确认结果（阶段 5 preflight 写入，快照投影读取）
    # ------------------------------------------------------------------ #

    @property
    def premise_result(self) -> dict[str, Any] | None:
        raw = self._state().get("premise_result")
        return raw if isinstance(raw, dict) else None

    def set_premise_result(self, payload: dict[str, Any]) -> None:
        self._state()["premise_result"] = payload

    def record_hypothesis_assessments(
        self, assessments: Sequence[ResearchHypothesisAssessment]
    ) -> None:
        """保存 finish_research 提交的假设评估，供快照和阶段 6 报告使用。"""

        existing = {item.hypothesis_id for item in self.hypothesis_assessments()}
        merged = list(self.hypothesis_assessments())
        for assessment in assessments:
            if assessment.hypothesis_id in existing:
                continue
            merged.append(assessment)
            existing.add(assessment.hypothesis_id)
        self._state()["hypothesis_assessments"] = [
            item.model_dump(mode="json") for item in merged
        ]

    def hypothesis_assessments(self) -> tuple[ResearchHypothesisAssessment, ...]:
        raw_items = self._state().get("hypothesis_assessments")
        if not isinstance(raw_items, list):
            return ()
        return tuple(
            ResearchHypothesisAssessment.model_validate(item) for item in raw_items
        )

    def set_hypothesis_audit(self, records: Sequence[HypothesisAuditRecord]) -> None:
        """保存服务端假设裁决审计（请求值 → 最终值），供快照与报告使用。"""

        self._state()["hypothesis_audit"] = [
            {
                "hypothesis_id": record.hypothesis_id,
                "requested": record.requested,
                "final": record.final,
                "downgraded": record.downgraded,
                "reason": record.reason,
            }
            for record in records
        ]

    def hypothesis_audit(self) -> tuple[HypothesisAuditRecord, ...]:
        raw_items = self._state().get("hypothesis_audit")
        if not isinstance(raw_items, list):
            return ()
        return tuple(HypothesisAuditRecord(**item) for item in raw_items)

    def set_report_inputs(self, payload: dict[str, Any]) -> None:
        """暂存已通过校验的 finish 报告输入，供最终报告构建复用。"""

        self._state()["report_inputs"] = payload

    def report_inputs(self) -> dict[str, Any] | None:
        raw = self._state().get("report_inputs")
        return raw if isinstance(raw, dict) else None

    # ------------------------------------------------------------------ #
    # ResultStore 引用
    # ------------------------------------------------------------------ #

    def result_set_payload(self, result_set_id: str) -> dict[str, Any] | None:
        result_sets = self.context.state.get(_RESULT_SETS_KEY)
        if not isinstance(result_sets, dict):
            return None
        payload = result_sets.get(result_set_id)
        return payload if isinstance(payload, dict) else None

    def merge_result_ref(self, ref: ResultSetRef) -> None:
        """把命名结果集引用合并进共享 state，键位与执行服务保持一致。"""

        result_sets = self.context.state.get(_RESULT_SETS_KEY)
        if not isinstance(result_sets, dict):
            result_sets = {}
        self.context.state[_RESULT_SETS_KEY] = {
            **result_sets,
            ref.result_set_id: ref.model_dump(mode="json"),
        }

    def execution_identity(self) -> tuple[str, int, int, int]:
        """ResultStore 读写所需的会话归属四元组。"""

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
        return (execution_id, chat_id, record_id, self.workspace_id)

    # ------------------------------------------------------------------ #
    # 内部状态访问
    # ------------------------------------------------------------------ #

    def _state(self) -> dict[str, Any]:
        state = self.context.state.setdefault(RESEARCH_STATE_KEY, {})
        if not isinstance(state, dict):
            raise TypeError("RESEARCH_TOOL_STATE_INVALID")
        return state

    def _evidence_map(self) -> dict[str, Any]:
        evidence = self._state().setdefault("evidence", {})
        if not isinstance(evidence, dict):
            raise TypeError("RESEARCH_TOOL_EVIDENCE_LEDGER_INVALID")
        return evidence

    def _observations(self) -> dict[str, Any]:
        observations = self._state().setdefault("observations", {})
        if not isinstance(observations, dict):
            raise TypeError("RESEARCH_TOOL_OBSERVATIONS_INVALID")
        return observations

    def _fingerprints(self) -> dict[str, Any]:
        fingerprints = self._state().setdefault("request_fingerprints", {})
        if not isinstance(fingerprints, dict):
            raise TypeError("RESEARCH_TOOL_FINGERPRINTS_INVALID")
        return fingerprints


__all__ = ["RESEARCH_STATE_KEY", "ResearchToolContext"]
