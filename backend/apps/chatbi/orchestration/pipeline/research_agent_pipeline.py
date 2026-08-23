"""Research Agent 主路径编排适配器（doc38 阶段 7.5 切流接线）。

把 :class:`ResearchAgentHarness` 接入 RunOrchestrator 分发，使
``research_execution_mode="agent"`` 成为可切换的真实引擎（§12.2 前置
条件 1 的代码侧前提）。适配器只做四件事：

1. 读取路由期冻结的执行输入，用与 shadow 双跑完全相同的确定性投影
   （``build_shadow_agent_requirement``）构造新契约 Requirement；
2. 用请求作用域服务装配 Harness——主路径与用户可见执行共享同一会话、
   注册表和生命周期，取消与澄清挂起真实可达；
3. run 行上已存在本 Run 冻结的新路径研究事实（含 ``requirement`` 的
   ``research_state``）时走恢复续跑而不是重开循环；
4. 把 Harness 终态翻译成统一生命周期事件：succeeded/partial 等报告态
   走 finish，failed 抛 :class:`ResearchPipelineError` 交分发层失败收口，
   cancelled 走取消收口。

失败一律显式报错，绝不静默回退旧 Research（§11.3.6）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research import ResearchRequirement
from apps.chatbi.orchestration.agent.lifecycle import AgentLifecycle
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.pipeline.research import ResearchPipelineError
from apps.chatbi.orchestration.pipeline.research_agent import (
    ResearchAgentHarness,
    ResearchAgentRunOutcome,
)
from apps.chatbi.services.research.run_lifecycle import load_research_state
from apps.chatbi.services.research.shadow import build_shadow_agent_requirement
from apps.event import RenderEvent
from apps.retrieval import ExecutableAssetReference, RetrievalResourceType
from apps.semantic.models.dto import DatasetSchema
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
            requirement = self._project_requirement(state)
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

    def _project_requirement(self, state: AgentRuntimeState) -> Any:
        """从路由冻结输入确定性投影新契约 Requirement。

        与 shadow 双跑共用同一投影函数与同一冻结输入（§1.1 同源图），
        保证 agent 主路径与被比较的影子侧行为一致。冻结输入缺失或无法
        投影时显式失败，绝不静默改走旧 Research。
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
                "agent 引擎要求路由期冻结的旧契约 Requirement，但执行输入中缺失。",
            )
        try:
            legacy_requirement = ResearchRequirement.model_validate(payload)
        except ValueError as exc:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_INVALID",
                f"路由冻结的旧契约 Requirement 无法加载：{exc}",
            ) from exc
        dataset_id = getattr(state.record, "dataset_id", None)
        try:
            return build_shadow_agent_requirement(
                legacy_requirement,
                run_id=f"research-{state.require_run_id()}",
                tenant_scope=f"oid:{int(state.run.oid)}",
                dataset_ref=f"ASSET:dataset:{dataset_id or 0}",
            )
        except ValueError as exc:
            raise ResearchPipelineError(
                "RESEARCH_AGENT_REQUIREMENT_PROJECTION_FAILED",
                str(exc),
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
        scope = (
            raw_scope
            if isinstance(raw_scope, SemanticAssetScope)
            else SemanticAssetScope.model_validate(raw_scope)
        )
        enriched = scope.model_copy(
            update={
                "schema_snapshot": DatasetSchema.model_validate(schema_payload),
                "scope_fingerprint": scope_fingerprint,
                "permission_fingerprint": permission_fingerprint,
                "allowed_assets": self._allowed_asset_references(allowed_asset_refs),
            }
        )
        stamped = enriched.model_dump(mode="json")
        state.context.state["semantic_scope"] = stamped
        return stamped

    @staticmethod
    def _governed_asset_refs(scope: Any) -> tuple[str, ...]:
        """从冻结治理 Scope 推导可执行资产引用集合。

        指标取目标 ∪ 驱动 ∪ 贡献，维度取 Scope 维度 ∪ 贡献维度，剔除
        ``excluded_asset_refs`` 后按引用去重。口径必须覆盖 semantic_query_builder
        的 SCOPE_DENIED 门允许的全部引用，否则合法查询会因 asset_map 缺项
        被误判 UNSUPPORTED_CAPABILITY。
        """

        def _get(key: str) -> list[str]:
            value = (
                scope.get(key) if isinstance(scope, dict) else getattr(scope, key, None)
            )
            return [item for item in (value or ()) if isinstance(item, str)]

        excluded = set(_get("excluded_asset_refs"))
        refs: list[str] = []
        for key in (
            "target_metric_refs",
            "driver_metric_refs",
            "contribution_metric_refs",
            "dimension_refs",
            "contribution_dimension_refs",
        ):
            refs.extend(item for item in _get(key) if item not in excluded)
        return tuple(dict.fromkeys(refs))

    @staticmethod
    def _allowed_asset_references(
        refs: tuple[str, ...],
    ) -> tuple[ExecutableAssetReference, ...]:
        """把 ``KIND:资产ID:模型ID`` 冻结引用物化为运行时可执行资产。"""

        references: list[ExecutableAssetReference] = []
        for ref in refs:
            parts = ref.split(":")
            if (
                len(parts) != 3
                or not parts[1].isdigit()
                or not parts[2].isdigit()
                or parts[0] not in {item.value for item in RetrievalResourceType}
            ):
                raise ResearchPipelineError(
                    "RESEARCH_AGENT_REQUIREMENT_INVALID",
                    f"冻结 Scope 含非法资产引用：{ref!r}",
                )
            references.append(
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType(parts[0]),
                    asset_id=int(parts[1]),
                    model_id=int(parts[2]),
                )
            )
        return tuple(references)

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
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
        return {
            "summary": outcome.completion.summary,
            "evidence_ids": list(outcome.completion.evidence_ids),
            "limitations": list(outcome.completion.limitations),
        }


__all__ = [
    "ResearchAgentPipeline",
    "ResearchAgentPipelineDependencies",
]
