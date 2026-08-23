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
from typing import TYPE_CHECKING, Any

from apps.chatbi.models.dto.analysis_plan import ResultSetRef
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchBudget,
    ResearchBudgetUsage,
    ResearchCompletion,
    ResearchEvidence,
    ResearchHypothesisAssessment,
    ToolObservation,
)
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
