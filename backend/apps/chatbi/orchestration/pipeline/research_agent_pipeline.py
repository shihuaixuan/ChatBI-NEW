"""Research Agent 主路径编排适配器（doc38 阶段 8 起唯一 Research 引擎）。

把 :class:`ResearchAgentHarness` 接入 RunOrchestrator 分发。适配器只做
四件事：

1. 读取路由期冻结的新契约 Requirement（``routing_freeze`` 直接产出，
   阶段 8 起不再经过旧契约投影），并按 Run 身份补盖 ``run_id``；
2. 用请求作用域服务装配 Harness——主路径与用户可见执行共享同一会话、
   注册表和生命周期，取消与澄清挂起真实可达；
3. run 行上已存在本 Run 冻结的研究事实（含 ``requirement`` 的
   ``research_state``）时走恢复续跑而不是重开循环；
4. 把 Harness 终态翻译成统一生命周期事件：succeeded/partial 等报告态
   走 finish，failed 抛 :class:`ResearchPipelineError` 交分发层失败收口，
   cancelled 走取消收口。

失败一律显式报错；旧 Research 管道已随阶段 8 删除，不存在回退目标
（§11.3.6 / §12.5）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.errors import ResearchPipelineError
from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import ResearchAgentRequirement
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.pipeline.research_agent import (
    ResearchAgentHarness,
    ResearchAgentRunOutcome,
)
from apps.chatbi.services.research.initial_plan import ResearchInitialPlanner
from apps.chatbi.services.research.routing_freeze import (
    research_permission_fingerprint,
)
from apps.chatbi.services.research.run_lifecycle import load_research_state
from apps.chatbi.services.research.scope_stamp import (
    allowed_asset_references,
    governed_asset_refs,
    stamp_semantic_scope,
)
from apps.event import RenderEvent
from apps.retrieval import ExecutableAssetReference
from apps.tool.tools.semantic_contracts import SemanticAssetScope
from apps.trace import AgentTraceRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchAgentPipelineDependencies:
    """主路径 Harness 所需的请求作用域服务。

    与 shadow 栈逐次重建不同，这里的会话/注册表/生命周期/事件发布都是
    当前请求的同一实例；只有随 Run 身份变化的 SemanticQueryRuntime 通过
    工厂在每次执行时构造。
    """

    config: AgentConfig
    session: Any
    lifecycle: AgentLifecycle
    model_client: Any
    result_store: Any
    recorder: AgentTraceRecorder
    semantic_runtime_factory: Callable[[Any, Any], Any]
    compute_engine_factory: Callable[[], Any]


class ResearchAgentPipeline:
    """新契约 Research Agent 的主路径编排适配器。"""

    def __init__(self, dependencies: ResearchAgentPipelineDependencies) -> None:
        self._deps = dependencies

    def run(self, state: AgentRuntimeState) -> Iterator[RenderEvent]:
        if self._has_frozen_agent_state(state):
            # 行上已有本 Run 冻结的新路径研究事实：恢复续跑，不重开。
            overlay = self._stamp_scope_from_frozen(state)
            harness = self._build_harness(state, overlay)
            logger.info(
                "chatbi.research_agent.resumed run=%s",
                state.require_run_id(),
            )
            outcome = harness.resume()
        else:
            requirement = self._load_frozen_requirement(state)
            stamped_scope = self._stamp_scope(
                state,
                scope_fingerprint=requirement.scope.scope_fingerprint,
                permission_fingerprint=requirement.version_snapshot.permission_fingerprint,
                allowed_asset_refs=self._governed_asset_refs(requirement.scope),
            )
            harness = self._build_harness(
                state,
                {
                    "semantic_scope": stamped_scope,
                    "permission_version": getattr(
                        state.context, "permission_version", None
                    ),
                },
            )
            outcome = harness.run(requirement=requirement)
        yield from self._finalize(state, outcome)

    # ------------------------------------------------------------------ #
    # 输入构造
    # ------------------------------------------------------------------ #

    def _load_frozen_requirement(self, state: AgentRuntimeState) -> Any:
        """加载路由期冻结的新契约 Requirement 并补盖本 Run 的 run_id。

        冻结载荷由 ``routing_freeze.freeze_research_requirement`` 在路由期
        直接产出；run_id 属 Run 身份而非路由事实，由适配层在此覆盖。载荷
        缺失、形状非法（含阶段 8 前的旧契约历史行）时显式失败。
        """

        execution = state.context.state.get("execution_requirement")
        payload = (
            execution.get("research_requirement")
            if isinstance(execution, dict)
            else None
        )
        if not isinstance(payload, dict):
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_MISSING",
                "agent 引擎要求路由期冻结的新契约 Requirement，但执行输入中缺失。",
            )
        stamped = {
            **payload,
            "run_id": f"research-{state.require_run_id()}",
        }
        try:
            return ResearchAgentRequirement.model_validate(stamped)
        except ValueError as exc:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_INVALID",
                f"路由冻结的 Requirement 无法加载：{exc}",
            ) from exc

    def _stamp_scope(
        self,
        state: AgentRuntimeState,
        *,
        scope_fingerprint: str,
        permission_fingerprint: str,
        allowed_asset_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """把冻结指纹、已发布 Schema 快照与治理资产集盖到路由期检索 Scope 上。

        Semantic Query 运行时边界校验要求 Scope 携带与冻结 Requirement
        一致的 scope/permission 指纹及 Schema 快照；检索工具产出的原始
        Scope 不携带这些字段（semantic_contracts 契约注明"缺失时由上层
        上下文提供"），且其 ``allowed_assets`` 恒为空（检索契约不列计划
        资产）。本适配层作为"上层上下文"补齐三者：资产集从冻结 Requirement
        的治理引用物化，口径与 builder 的 SCOPE_DENIED 门一致，不扩大权限。
        返回盖戳后的 Scope 载荷，供注入 Harness 工具上下文。
        """

        execution = state.context.state.get("execution_requirement")
        schema_payload = (
            execution.get("asset_snapshot", {}).get("dataset_schema")
            if isinstance(execution, dict)
            else None
        )
        if not isinstance(schema_payload, dict):
            raise ResearchPipelineError(
                "RESEARCH_AGENT_SCHEMA_SNAPSHOT_MISSING",
                "agent 引擎要求路由期冻结的 DatasetSchema 快照，但执行输入中缺失。",
            )
        raw_scope = state.context.state.get("semantic_scope")
        if raw_scope is None:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_SCOPE_MISSING",
                "agent 引擎要求路由期检索 Scope，但执行输入中缺失。",
            )
        try:
            resolved_scope = SemanticAssetScope.model_validate(raw_scope)
        except ValueError as exc:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_SCOPE_INVALID",
                f"路由期检索 Scope 无法加载：{exc}",
            ) from exc
        schema_fingerprint = schema_payload.get("schema_fingerprint")
        if not isinstance(schema_fingerprint, str) or not schema_fingerprint:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_SCHEMA_SNAPSHOT_INVALID",
                "路由期 DatasetSchema 快照缺少 schema_fingerprint。",
            )
        current_permission_fingerprint = research_permission_fingerprint(
            schema_fingerprint=schema_fingerprint,
            scope_fingerprint=scope_fingerprint,
            tenant_scope=f"oid:{resolved_scope.workspace_id}",
            dataset_ref=f"ASSET:dataset:{resolved_scope.dataset_id}",
            user_id=resolved_scope.user_id,
            datasource_id=resolved_scope.datasource_id,
            permission_version=resolved_scope.permission_version,
            authorized_tables=resolved_scope.authorized_tables,
        )
        if current_permission_fingerprint != permission_fingerprint:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_PERMISSION_FINGERPRINT_CHANGED",
                "当前身份或数据权限与路由期冻结快照不一致，拒绝继续执行。",
            )
        stamped = stamp_semantic_scope(
            scope=resolved_scope,
            schema_payload=schema_payload,
            scope_fingerprint=scope_fingerprint,
            permission_fingerprint=permission_fingerprint,
            allowed_asset_refs=allowed_asset_refs,
        )
        state.context.state["semantic_scope"] = stamped
        return stamped

    @staticmethod
    def _governed_asset_refs(scope: Any) -> tuple[str, ...]:
        """委托共享实现；口径说明见 :func:`scope_stamp.governed_asset_refs`。"""

        return governed_asset_refs(scope)

    @staticmethod
    def _allowed_asset_references(
        refs: tuple[str, ...],
    ) -> tuple[ExecutableAssetReference, ...]:
        """委托共享实现；非法引用按管道契约显式失败（fail-loud 不变）。"""

        try:
            return allowed_asset_references(refs)
        except ValueError as exc:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_INVALID",
                str(exc),
            ) from exc

    def _stamp_scope_from_frozen(self, state: AgentRuntimeState) -> dict[str, Any]:
        """恢复续跑前按行上冻结的新契约 Requirement 补齐 Scope 指纹。"""

        requirement = load_research_state(state.run.derived_state).get("requirement")
        if not isinstance(requirement, dict):
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_MISSING",
                "恢复续跑要求行上已冻结的新路径 Requirement，但缺失。",
            )
        scope = requirement.get("scope") or {}
        version = requirement.get("version_snapshot") or {}
        scope_fingerprint = scope.get("scope_fingerprint")
        permission_fingerprint = version.get("permission_fingerprint")
        if not isinstance(scope_fingerprint, str) or not isinstance(
            permission_fingerprint, str
        ):
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_INVALID",
                "冻结 Requirement 缺少 scope/permission 指纹，无法续跑。",
            )
        return self._stamp_scope(
            state,
            scope_fingerprint=scope_fingerprint,
            permission_fingerprint=permission_fingerprint,
            allowed_asset_refs=self._governed_asset_refs(scope),
        )

    def _has_frozen_agent_state(self, state: AgentRuntimeState) -> bool:
        """run 行是否携带本 Run 冻结的新路径研究事实。

        新旧路径共用 ``research_state`` 键但载荷形状不同：新路径由
        ``requirement`` 字段锚定冻结契约；缺该字段即视为没有可续跑的
        新路径事实（含旧行），一律开新循环。
        """

        research_state = load_research_state(state.run.derived_state)
        return isinstance(research_state.get("requirement"), dict)

    def _build_harness(
        self,
        state: AgentRuntimeState,
        context_state_overlay: dict[str, Any] | None = None,
    ) -> ResearchAgentHarness:
        """组装研究循环宿主；``context_state_overlay`` 携带路由期冻结的
        上层上下文（semantic_scope 等），供工具上下文初始化时合并。"""
        deps = self._deps
        return ResearchAgentHarness(
            session=deps.session,
            config=deps.config,
            run_row=state.run,
            record=state.record,
            model_client=deps.model_client,
            semantic_runtime=deps.semantic_runtime_factory(state.run, state.record),
            context_state_overlay=context_state_overlay,
            compute_engine=deps.compute_engine_factory(),
            result_store=deps.result_store,
            recorder=deps.recorder,
            cancellation=getattr(state, "cancellation", None),
            initial_planner=ResearchInitialPlanner(),
        )

    # ------------------------------------------------------------------ #
    # 终态翻译
    # ------------------------------------------------------------------ #

    def _finalize(
        self,
        state: AgentRuntimeState,
        outcome: ResearchAgentRunOutcome,
    ) -> Iterator[RenderEvent]:
        completion = outcome.completion
        if completion.status == "cancelled":
            # cancel_research_run 只收口研究事实；Run 与 ChatRecord 的取消
            # 终态由通用生命周期负责（run_lifecycle 契约）。
            yield from self._deps.lifecycle.finalize_cancellation(
                state,
                completion.summary or "用户已请求取消运行",
                stage=f"research_loop:{outcome.stop_reason}",
            )
            return
        if completion.status == "failed":
            raise ResearchPipelineError(
                f"RESEARCH_{completion.reason.value.upper()}",
                completion.summary,
            )
        answer = self._answer_payload(outcome)
        yield from self._deps.lifecycle.finish(
            state,
            answer=json.dumps(answer, ensure_ascii=False, sort_keys=True),
            chart={},
            sql=None,
        )

    @staticmethod
    def _answer_payload(outcome: ResearchAgentRunOutcome) -> dict[str, Any]:
        """优先级 final_report > report_draft > 完成态摘要。"""

        snapshot = outcome.snapshot
        for key in ("final_report", "report_draft"):
            raw = getattr(snapshot, key, None) if snapshot is not None else None
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise ResearchPipelineError(
                    "RESEARCH_AGENT_REPORT_INVALID",
                    f"研究报告 {key} 不是合法 JSON，不能降级为未校验摘要。",
                ) from exc
            if isinstance(payload, dict):
                return payload
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REPORT_INVALID",
                f"研究报告 {key} 必须是 JSON 对象。",
            )
        return {
            "summary": outcome.completion.summary,
            "evidence_ids": list(outcome.completion.evidence_ids),
            "limitations": list(outcome.completion.limitations),
        }

__all__ = [
    "ResearchAgentPipeline",
    "ResearchAgentPipelineDependencies",
]
