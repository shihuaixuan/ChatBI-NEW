"""阶段 7：Shadow 双跑的执行隔离（doc38 §11.3.1）。

本模块是 Shadow 路径的唯一落地边界，包含三件事：

- ``build_shadow_agent_requirement``：把路由入口冻结的旧契约
  ``ResearchRequirement`` 确定性地投影成新路径 ``ResearchAgentRequirement``。
  除 ``run_id``（追加 ``-shadow`` 后缀）和新契约必填的治理字段外，
  goal、目标指标、不可变筛选、时间绑定、Scope 和版本快照全部逐字段相同，
  因此 Semantic Query plan ID（含 run_id 的哈希）自动与主路径不同；
- ``ResearchExecutionState``：把 ``ResearchToolContext`` 投影成
  ``AnalysisExecutionService`` 需要的状态表面。事件、Trace 和结果工件都按
  该状态的 run id（shadow 行）落库，用户 SSE 只跟随主 run id，天然不可见；
  PROVEN 门禁、权限和资源限制原样生效，不做任何放宽；
- ``ShadowRunner``：在独立数据库会话里创建 shadow ``ChatbiAgentRun`` 行
  （``derived_state.shadow`` 标记 + 父 run id），组装宿主注入的执行栈并
  双跑新路径。任何异常都收敛到 shadow 行自身，绝不影响主路径事务、
  生命周期或 ``last_execution``。

隔离不变量（§11.3.1）：不发布用户可见回答事件；不调用主路径生命周期
finish；不修改主路径 ``last_execution``；plan/Tool Call/Result Artifact
标识独立；查询仍经过真实权限和资源限制。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apps.chatbi.models import ChatbiAgentRun
from apps.chatbi.models.dto.research import (
    ResearchBudget as LegacyResearchBudget,
)
from apps.chatbi.models.dto.research import (
    ResearchDriverRelationship as LegacyDriverRelationship,
)
from apps.chatbi.models.dto.research import (
    ResearchRequirement,
)
from apps.chatbi.models.dto.research import (
    ResearchScope as LegacyScope,
)
from apps.chatbi.models.dto.research import (
    ResearchTimeBinding as LegacyTimeBinding,
)
from apps.chatbi.models.dto.research import (
    ResearchVersionSnapshot as LegacyVersionSnapshot,
)
from apps.chatbi.models.dto.research_agent import (
    ResearchAgentRequirement,
    ResearchBudget,
    ResearchDriverRelationship,
    ResearchEvidenceRequirement,
    ResearchHierarchy,
    ResearchImmutableFilter,
    ResearchPremise,
    ResearchScope,
    ResearchTimeBinding,
    ResearchTimeRole,
    ResearchVersionSnapshot,
)
from apps.chatbi.models.dto.research_agent import (
    ResearchReason as AgentResearchReason,
)
from apps.tool import NeverCancelled

SHADOW_RUN_ID_SUFFIX = "-shadow"
# derived_state 中 shadow 标记的键名；比较器与运维查询都依赖该键。
SHADOW_MARKER_KEY = "shadow"
_SHADOW_STATE_EXCLUDED_KEYS = frozenset(
    {"full_data", "tool_offloads", "semantic_schema"}
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def shadow_run_id(base: str) -> str:
    """派生 shadow run id；长度超限时报错而不是静默截断。"""

    derived = f"{base}{SHADOW_RUN_ID_SUFFIX}"
    if len(derived) > 128:
        raise ValueError("RESEARCH_SHADOW_RUN_ID_TOO_LONG")
    return derived


def build_shadow_agent_requirement(
    legacy_requirement: ResearchRequirement,
    *,
    run_id: str,
    tenant_scope: str,
    dataset_ref: str,
    premise: ResearchPremise | None = None,
) -> ResearchAgentRequirement:
    """把冻结的旧契约 Requirement 投影成新路径 Requirement。

    投影是确定性的：同一份 legacy 输入永远得到同一份新契约输出。
    无法投影的字段（未知时间角色等）直接报错，由调用方按切流策略处理，
    绝不静默放宽 Scope。
    """

    legacy_version = legacy_requirement.version_snapshot
    version_snapshot = ResearchVersionSnapshot(
        schema_version=legacy_version.schema_version,
        contract_version=legacy_version.contract_version,
        schema_fingerprint=legacy_version.schema_fingerprint,
        scope_fingerprint=legacy_version.scope_fingerprint,
        permission_fingerprint=_permission_fingerprint(
            legacy_version,
            tenant_scope=tenant_scope,
            dataset_ref=dataset_ref,
        ),
    )
    scope = _project_scope(
        legacy_requirement.scope,
        tenant_scope=tenant_scope,
        dataset_ref=dataset_ref,
        scope_fingerprint=legacy_version.scope_fingerprint,
        time_roles=legacy_requirement.time_roles,
        extra_dimension_refs=_binding_dimension_refs(legacy_requirement),
    )
    time_bindings = tuple(
        _project_time_binding(item) for item in legacy_requirement.time_bindings
    )
    immutable_filters = tuple(
        ResearchImmutableFilter(
            target_ref=item.target_ref,
            operator=item.operator,
            value=item.value,
        )
        for item in legacy_requirement.immutable_filters
    )
    evidence_requirements = list(_default_evidence_requirements(legacy_requirement))
    if premise is not None:
        evidence_requirements.insert(
            0,
            ResearchEvidenceRequirement(
                requirement_id="premise",
                kind="premise_confirmation",
                description="确认前提描述的变化事实",
                required_asset_refs=(premise.metric_ref,),
            ),
        )
    return ResearchAgentRequirement(
        run_id=run_id,
        goal=legacy_requirement.goal,
        reason=AgentResearchReason(legacy_requirement.reason.value),
        target_metric_refs=legacy_requirement.target_metric_refs,
        premise_to_verify=premise,
        time_bindings=time_bindings,
        time_bindings_by_model={
            model_id: tuple(
                _project_time_binding(item) for item in legacy_bindings
            )
            for model_id, legacy_bindings in (
                legacy_requirement.time_bindings_by_model or {}
            ).items()
        },
        immutable_filters=immutable_filters,
        scope=scope,
        evidence_requirements=tuple(evidence_requirements),
        budget=_project_budget(legacy_requirement.budget),
        version_snapshot=version_snapshot,
    )


def _permission_fingerprint(
    legacy_version: LegacyVersionSnapshot,
    *,
    tenant_scope: str,
    dataset_ref: str,
) -> str:
    payload = {
        "schema_fingerprint": legacy_version.schema_fingerprint,
        "scope_fingerprint": legacy_version.scope_fingerprint,
        "tenant_scope": tenant_scope,
        "dataset_ref": dataset_ref,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def _binding_dimension_refs(legacy_requirement: ResearchRequirement) -> tuple[str, ...]:
    """收集全部时间绑定引用的维度，供投影并进 Scope.dimension_refs。"""

    bindings = [
        *legacy_requirement.time_bindings,
        *(
            binding
            for grouped in (legacy_requirement.time_bindings_by_model or {}).values()
            for binding in grouped
        ),
    ]
    return tuple(
        dict.fromkeys(
            binding.dimension_ref for binding in bindings if binding.dimension_ref
        )
    )


def _project_scope(
    legacy_scope: LegacyScope,
    *,
    tenant_scope: str,
    dataset_ref: str,
    scope_fingerprint: str,
    time_roles: tuple[str, ...],
    extra_dimension_refs: tuple[str, ...] = (),
) -> ResearchScope:
    resolved_time_roles = tuple(
        role for role in time_roles if role in {item.value for item in ResearchTimeRole}
    )
    if not resolved_time_roles:
        # 驱动关系要求至少一个可投影时间角色；投影失败必须显式暴露。
        raise ValueError("RESEARCH_SHADOW_TIME_ROLE_UNPROJECTABLE")
    relationships = tuple(
        _project_relationship(item, time_roles=resolved_time_roles)
        for item in legacy_scope.driver_relationships
    )
    hierarchies = tuple(
        ResearchHierarchy(hierarchy_id=item.id, dimension_refs=item.dimension_refs)
        for item in legacy_scope.hierarchies
    )
    return ResearchScope(
        target_metric_refs=legacy_scope.target_metric_refs,
        # 新契约要求时间绑定的维度必须在 Scope 内；旧冻结把默认时间维度单独
        # 挂在 time_bindings 上而不列入 scope.dimension_refs，这里做确定性并集。
        dimension_refs=tuple(
            dict.fromkeys((*legacy_scope.dimension_refs, *extra_dimension_refs))
        ),
        driver_metric_refs=legacy_scope.driver_metric_refs,
        allowed_filter_refs=legacy_scope.allowed_filter_refs,
        hierarchies=hierarchies,
        driver_relationships=relationships,
        contribution_metric_refs=legacy_scope.contribution_metric_refs,
        contribution_dimension_refs=legacy_scope.contribution_dimension_refs,
        contribution_tolerance=legacy_scope.contribution_tolerance,
        excluded_asset_refs=legacy_scope.excluded_asset_refs,
        tenant_scope=tenant_scope,
        dataset_ref=dataset_ref,
        scope_fingerprint=scope_fingerprint,
    )


def _project_relationship(
    relationship: LegacyDriverRelationship,
    *,
    time_roles: tuple[str, ...],
) -> ResearchDriverRelationship:
    return ResearchDriverRelationship(
        target_metric_ref=relationship.target_metric_ref,
        driver_metric_ref=relationship.driver_metric_ref,
        component_metric_refs=relationship.component_metric_refs,
        relationship_type=relationship.relationship_type,
        validation_method=relationship.validation_method,
        expected_direction=relationship.expected_direction,
        formula_definition=relationship.formula_definition,
        dimension_refs=relationship.dimension_refs,
        dimension_refs_by_model=dict(relationship.dimension_refs_by_model or {}),
        relation_path=tuple(relationship.relation_path or ()),
        time_roles=resolved_relationship_time_roles(time_roles),
        relationship_fingerprint=relationship.relationship_fingerprint,
    )


def resolved_relationship_time_roles(
    time_roles: tuple[str, ...],
) -> tuple[ResearchTimeRole, ...]:
    return tuple(ResearchTimeRole(role) for role in time_roles)


def _project_time_binding(binding: LegacyTimeBinding) -> ResearchTimeBinding:
    return ResearchTimeBinding(
        role=ResearchTimeRole(binding.role),
        expression=binding.expression,
        dimension_ref=binding.dimension_ref,
        normalized=binding.normalized,
    )


def _project_budget(budget: LegacyResearchBudget) -> Any:
    return ResearchBudget(
        max_iterations=budget.max_iterations,
        max_queries=budget.max_queries,
        max_model_calls=budget.max_model_calls,
        max_duration_seconds=budget.max_duration_seconds,
        max_evidence_rows=budget.max_evidence_rows,
        max_evidence_chars=budget.max_evidence_chars,
    )


def _default_evidence_requirements(
    legacy_requirement: ResearchRequirement,
) -> tuple[ResearchEvidenceRequirement, ...]:
    """确定性生成完成目标所需的最低证据类型。

    不复制旧路径的动作白名单：新路径由服务端完成度门禁判断证据是否充分，
    这里只声明"目标指标分析"这一最低要求；前提确认在存在前提时补充。
    """

    return (
        ResearchEvidenceRequirement(
            requirement_id="target-analysis",
            kind="dimension_or_driver_analysis",
            description="围绕目标指标完成维度或驱动归因分析",
            required_asset_refs=legacy_requirement.target_metric_refs,
        ),
    )


class _ExecutionDeadlineBudget:
    """把新契约 ``ResearchBudget`` 适配成执行服务的墙钟预算表面。

    ``AnalysisExecutionService`` 读取 ``state.budget.remaining_seconds()`` 计算
    查询任务截止（run 1278 冒烟回归：投影态缺 budget 直接 AttributeError）。
    研究循环的整跑时长由 Harness 按 ``max_duration_seconds`` 单独约束，这里
    以每次执行构造时刻起算，只保证单次查询的截止不超过剩余时长上限。
    """

    def __init__(self, budget: Any) -> None:
        self._budget = budget
        self._started_at = time.monotonic()

    def remaining_seconds(self) -> float:
        limit = float(getattr(self._budget, "max_duration_seconds", 0) or 0)
        if limit <= 0:
            return float("inf")
        return max(limit - (time.monotonic() - self._started_at), 0.0)


@dataclass
class ResearchExecutionState:
    """把 ResearchToolContext 投影成 AnalysisExecutionService 的状态表面。

    字段口径与 ``AgentRuntimeState`` 一致：run id 来自 shadow 行，
    working state 与会话来自底层 AgentToolContext。事件、Trace、计划快照
    和结果工件因此全部落在 shadow run 身份下。
    """

    ctx: Any
    run: ChatbiAgentRun
    record: Any

    def require_run_id(self) -> int:
        if self.run.id is None:
            raise RuntimeError("AGENT_RUN_ID_MISSING")
        return self.run.id

    @property
    def budget(self) -> Any:
        """执行服务读取的墙钟预算表面；见 :class:`_ExecutionDeadlineBudget`。"""

        return _ExecutionDeadlineBudget(getattr(self.ctx, "budget", None))

    @property
    def context(self) -> Any:
        return self.ctx.context

    @property
    def cancellation(self) -> Any:
        return self.ctx.cancellation or NeverCancelled()

    def persistable_context(self) -> dict[str, Any]:
        """与 AgentRuntimeState.persistable_context 同口径的持久化快照。"""

        state = self.ctx.context.state
        if not isinstance(state, dict):
            return {}
        return {
            key: value
            for key, value in state.items()
            if key not in _SHADOW_STATE_EXCLUDED_KEYS
        }


@dataclass
class ShadowRunMaterial:
    """启动一次 shadow 双跑所需的全部冻结输入。"""

    parent_run_id: int
    oid: int
    chat_id: int
    record_id: int
    user_id: int | None
    dataset_id: int | None
    temporal_context: dict[str, Any]
    legacy_requirement: ResearchRequirement
    premise: ResearchPremise | None = None

    def base_run_id(self) -> str:
        return f"research-{self.parent_run_id}"

    def tenant_scope(self) -> str:
        return f"oid:{self.oid}"

    def dataset_ref(self) -> str:
        return f"ASSET:dataset:{self.dataset_id or 0}"


@dataclass
class ShadowRunResult:
    status: str
    agent_run_id: int | None
    error: str | None = None
    duration_ms: int | None = None


class ShadowRunner:
    """在隔离栈中双跑新路径；所有副作用只落在 shadow run 身份上。

    ``harness_factory(session, run_row, record, requirement)`` 由宿主注入，
    返回带 ``run(requirement=..., ctx=None)`` 的研究循环宿主。Runner 自己
    不装配模型客户端和执行服务，保证依赖方向 services ← orchestration。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        harness_factory: Callable[[Any, Any, Any, ResearchAgentRequirement], Any],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._harness_factory = harness_factory
        self._now = now or (lambda: datetime.now(timezone.utc))

    def run_shadow(self, material: ShadowRunMaterial) -> ShadowRunResult:
        started_at = self._now()
        run_id = shadow_run_id(material.base_run_id())
        requirement = build_shadow_agent_requirement(
            material.legacy_requirement,
            run_id=run_id,
            tenant_scope=material.tenant_scope(),
            dataset_ref=material.dataset_ref(),
            premise=material.premise,
        )
        with self._session_factory() as session:
            row = self._start_row(session, material, requirement=requirement)
            try:
                harness = self._harness_factory(
                    session, row, self._record_stub(material), requirement
                )
                outcome = harness.run(requirement=requirement)
            except Exception as exc:  # 隔离边界：shadow 失败绝不上抛影响主路径。
                session.rollback()
                message = str(exc) or exc.__class__.__name__
                self._finish_row(session, row, status="failed", error=message)
                duration_ms = int((self._now() - started_at).total_seconds() * 1000)
                return ShadowRunResult(
                    status="failed", agent_run_id=None, error=message, duration_ms=duration_ms
                )
            summary = _outcome_summary(outcome)
            self._finish_row(
                session,
                row,
                status="finished",
                summary=summary,
                snapshot=_outcome_snapshot(outcome),
            )
            duration_ms = int((self._now() - started_at).total_seconds() * 1000)
            return ShadowRunResult(
                status="finished", agent_run_id=row.id, duration_ms=duration_ms
            )

    # ------------------------------------------------------------------ #
    # shadow 行生命周期（独立于主路径 lifecycle）
    # ------------------------------------------------------------------ #

    def _shadow_marker(self, material: ShadowRunMaterial) -> dict[str, Any]:
        return {
            "parent_run_id": material.parent_run_id,
            "mode": "shadow",
            "enabled": True,
        }

    def _record_stub(self, material: ShadowRunMaterial) -> Any:
        class _RecordStub:
            id = material.record_id
            datasource = material.dataset_id
            dataset_id = material.dataset_id
            question = ""

        return _RecordStub()

    def _start_row(
        self,
        session: Any,
        material: ShadowRunMaterial,
        *,
        requirement: ResearchAgentRequirement,
    ) -> ChatbiAgentRun:
        row = ChatbiAgentRun(
            oid=material.oid,
            chat_id=material.chat_id,
            record_id=material.record_id,
            status="running",
            execution_mode="research",
            temporal_context=material.temporal_context,
            config={"research_execution_mode": "shadow"},
            created_by=material.user_id,
        )
        marker = self._shadow_marker(material)
        # 冻结输入随行持久化：比较器据此核对双跑是否使用同一份目标与范围。
        marker["requirement"] = requirement.model_dump(mode="json")
        row.derived_state = {SHADOW_MARKER_KEY: marker}
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    def _finish_row(
        self,
        session: Any,
        row: ChatbiAgentRun,
        *,
        status: str,
        error: str | None = None,
        summary: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        derived = dict(row.derived_state or {})
        if derived.get(SHADOW_MARKER_KEY) is None:
            return
        marker = dict(derived.get(SHADOW_MARKER_KEY) or {})
        marker["status"] = status
        marker["finished_at"] = self._now().isoformat()
        if error is not None:
            marker["error"] = error
        if summary is not None:
            marker["summary"] = summary
        if snapshot is not None:
            # 与主路径 research_agent 管道同键：比较器按同一口径读取两侧。
            derived["research_run_snapshot"] = snapshot
        derived[SHADOW_MARKER_KEY] = marker
        row.derived_state = derived
        row.status = "finished" if status == "finished" else "failed"
        row.updated_at = self._now()
        session.commit()


def _outcome_summary(outcome: Any) -> dict[str, Any]:
    """提取可比较的运行摘要；字段缺失时保持为空而不猜测。"""

    summary: dict[str, Any] = {}
    for field in ("status", "finish_reason", "iterations"):
        value = getattr(outcome, field, None)
        if value is not None:
            summary[field] = (
                value.value if hasattr(value, "value") else value
            )
    snapshot = getattr(outcome, "snapshot", None)
    usage = getattr(snapshot, "budget_usage", None) if snapshot is not None else None
    if usage is not None:
        summary["queries_used"] = getattr(usage, "queries", None)
        summary["model_calls_used"] = getattr(usage, "model_calls", None)
    return summary


def _outcome_snapshot(outcome: Any) -> dict[str, Any] | None:
    snapshot = getattr(outcome, "snapshot", None)
    if snapshot is None or not hasattr(snapshot, "model_dump"):
        return None
    dumped: dict[str, Any] = snapshot.model_dump(mode="json")
    return dumped


def spawn_shadow_run(
    runner: ShadowRunner,
    material: ShadowRunMaterial,
    *,
    thread_factory: Callable[..., Any] | None = None,
) -> bool:
    """把 shadow 双跑放入后台线程；启动失败只降级为主路径照常执行。

    返回是否成功启动。线程为 daemon：进程退出时不阻塞主请求收口。
    """

    factory = thread_factory or threading.Thread

    def _body() -> None:
        runner.run_shadow(material)

    try:
        thread = factory(target=_body, daemon=True, name="chatbi-shadow-run")
        thread.start()
        return True
    except Exception:
        # 切流安全方向：shadow 启动失败绝不影响用户可见路径。
        return False


__all__ = [
    "SHADOW_MARKER_KEY",
    "SHADOW_RUN_ID_SUFFIX",
    "ResearchExecutionState",
    "ResearchAgentRequirement",
    "ShadowRunMaterial",
    "ShadowRunner",
    "ShadowRunResult",
    "build_shadow_agent_requirement",
    "shadow_run_id",
    "spawn_shadow_run",
]
