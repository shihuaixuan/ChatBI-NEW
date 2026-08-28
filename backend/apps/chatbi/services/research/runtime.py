"""Research ReAct 工具注册和执行边界。

这个 Runtime 只处理阶段 3 的通用执行规则，不负责 Agent 多轮循环、Evidence
生成或最终回答。这样阶段 4 至阶段 7 可以逐步接入具体工具，而不需要再次复制
存在性、可见性、预算、取消、重复和结果信封逻辑。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from apps.chatbi.errors import ResearchToolExecutionError
from apps.chatbi.models.dto.research_agent import (
    AttemptSummary,
    ComputeEvidenceAction,
    ExecutionError,
    FinishResearchAction,
    QuerySemanticDataAction,
    ReadEvidenceRowsAction,
    RemainingBudget,
    RequestClarificationAction,
    ResearchAction,
    ResearchActionType,
    ResearchExecutionErrorStage,
    ResearchStateStatus,
    ResearchTurnDecision,
    SearchSemanticAssetsAction,
    ToolResult,
    ToolResultStatus,
)
from apps.chatbi.services.research.action_fingerprint import (
    research_action_fingerprint,
)
from apps.chatbi.services.research.ports import (
    PreparedResearchAction,
    ResearchTool,
    ResearchToolCostEstimate,
)
from apps.chatbi.services.research.tool_errors import (
    map_research_tool_error,
)
from apps.chatbi.services.research.tool_result_persistence import (
    restore_research_tool_result,
)
from apps.tool.definition import ToolAnnotations, ToolDefinition

_ACTION_MODELS = {
    ResearchActionType.QUERY_SEMANTIC_DATA.value: QuerySemanticDataAction,
    ResearchActionType.COMPUTE_EVIDENCE.value: ComputeEvidenceAction,
    ResearchActionType.READ_EVIDENCE_ROWS.value: ReadEvidenceRowsAction,
    ResearchActionType.SEARCH_SEMANTIC_ASSETS.value: SearchSemanticAssetsAction,
    ResearchActionType.REQUEST_CLARIFICATION.value: RequestClarificationAction,
    ResearchActionType.FINISH_RESEARCH.value: FinishResearchAction,
}

_READ_ONLY_ACTIONS = frozenset(
    {
        ResearchActionType.QUERY_SEMANTIC_DATA.value,
        ResearchActionType.READ_EVIDENCE_ROWS.value,
    }
)

_CALL_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"


class ResearchToolRegistry:
    """Research 子域自己的工具白名单，不改变旧 ``apps.tool`` 注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ResearchTool[Any, Any, Any]] = {}

    def register(self, tool: ResearchTool[Any, Any, Any]) -> None:
        name = _tool_name(tool)
        if name in self._tools:
            raise ValueError(f"Duplicate research tool: {name}")
        args_model = getattr(tool, "args_model", None)
        if not isinstance(args_model, type) or not issubclass(args_model, BaseModel):
            raise TypeError(f"RESEARCH_TOOL_ARGS_MODEL_REQUIRED:{name}")
        result_model = getattr(tool, "result_model", None)
        if not isinstance(result_model, type) or not issubclass(
            result_model, BaseModel
        ):
            raise TypeError(f"RESEARCH_TOOL_RESULT_MODEL_REQUIRED:{name}")
        self._tools[name] = tool

    def get(self, name: str | ResearchActionType) -> ResearchTool[Any, Any, Any] | None:
        return self._tools.get(_name_value(name))

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def definitions(
        self,
        allowed: Collection[str] | None = None,
    ) -> list[ToolDefinition]:
        """把 Research 工具适配为 AgentReasoner 使用的通用 Schema。"""

        allow = set(allowed) if allowed is not None else None
        definitions: list[ToolDefinition] = []
        descriptions = {
            ResearchActionType.QUERY_SEMANTIC_DATA.value: "执行受治理的语义数据查询。",
            ResearchActionType.COMPUTE_EVIDENCE.value: "基于当前 Run 的 Evidence 执行确定性计算。",
            ResearchActionType.READ_EVIDENCE_ROWS.value: "读取当前 Run 已有 Evidence 的结果行。",
            ResearchActionType.SEARCH_SEMANTIC_ASSETS.value: "在冻结权限范围内补充语义资产。",
            ResearchActionType.REQUEST_CLARIFICATION.value: "向用户请求必要的分析口径澄清。",
            ResearchActionType.FINISH_RESEARCH.value: "提交当前研究的结束判断。",
        }
        for name, tool in self._tools.items():
            if allow is not None and name not in allow:
                continue
            read_only = name != ResearchActionType.FINISH_RESEARCH.value
            definitions.append(
                ToolDefinition(
                    name=name,
                    title=name,
                    description=descriptions.get(name, f"执行 Research 工具 {name}。"),
                    input_schema=tool.args_model.model_json_schema(),
                    output_schema=tool.result_model.model_json_schema(),
                    annotations=ToolAnnotations(
                        read_only=read_only,
                        idempotent=True,
                    ),
                )
            )
        return definitions


class ResearchToolRuntime:
    """执行单个或一批 Research 动作，并统一构造 ToolResult。"""

    def __init__(
        self,
        registry: ResearchToolRegistry,
        *,
        max_workers: int = 4,
        persist_result: Any = None,
        version_snapshot: Any = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("RESEARCH_TOOL_MAX_WORKERS_INVALID")
        self._registry = registry
        self._max_workers = max_workers
        self._persist_result = persist_result
        self._version_snapshot = version_snapshot
        self._cache: dict[str, ToolResult[Any]] = {}
        self._consumed = ResearchToolCostEstimate()
        self._lock = Lock()

    def execute(
        self,
        action: ResearchAction,
        *,
        context: Any,
        tool_call_id: str,
        visible_tools: Collection[str | ResearchActionType] | None = None,
        run_status: ResearchStateStatus | str = ResearchStateStatus.RUNNING,
        remaining_budget: RemainingBudget | None = None,
        cancelled: Any = False,
    ) -> ToolResult[Any]:
        """执行一个已经解析的动作。"""

        action_type = action.action_type
        tool = self._registry.get(action_type)
        if tool is None:
            return self._finalize_result(
                context,
                action,
                self._failure(
                    tool_call_id,
                    action_type,
                    ResearchToolExecutionError(
                        ResearchToolExecutionError.TOOL_NOT_FOUND,
                        f"工具 {action_type.value} 未注册",
                    ),
                ),
            )

        precondition_error = self._check_preconditions(
            action_type,
            tool_call_id,
            visible_tools=visible_tools,
            run_status=run_status,
            cancelled=cancelled,
        )
        if precondition_error is not None:
            return self._finalize_result(context, action, precondition_error)

        try:
            args = tool.args_model.model_validate(
                action.arguments.model_dump(mode="json")
            )
        except ValidationError as exc:
            return self._finalize_result(
                context,
                action,
                self._failure(
                    tool_call_id,
                    action_type,
                    exc,
                    default_stage=ResearchExecutionErrorStage.VALIDATION,
                ),
            )

        # 先按动作指纹读取已提交成功结果，再进入 prepare。这样澄清请求在
        # 进程恢复后即使仍处于 waiting 状态，也不会再次触发“已有请求”的拒绝。
        initial_fingerprint = self.action_fingerprint(action)
        with self._lock:
            cached_before_prepare = self._cache.get(initial_fingerprint)
        if cached_before_prepare is None:
            cached_before_prepare = _restore_context_result(
                context,
                fingerprint=initial_fingerprint,
                result_model=tool.result_model,
                replacement_tool_call_id=tool_call_id,
            )
            if cached_before_prepare is not None:
                with self._lock:
                    self._cache[initial_fingerprint] = cached_before_prepare
        if cached_before_prepare is not None and cached_before_prepare.status in {
            ToolResultStatus.SUCCEEDED,
            ToolResultStatus.WAITING_FOR_USER,
        }:
            return self._finalize_result(
                context,
                action,
                cached_before_prepare.model_copy(update={"tool_call_id": tool_call_id}),
            )

        try:
            prepared = tool.prepare(context, args)
            self._validate_prepared(prepared)
        except (
            ResearchToolExecutionError,
            ValidationError,
            PermissionError,
            ValueError,
        ) as exc:
            return self._finalize_result(
                context,
                action,
                self._failure(
                    tool_call_id,
                    action_type,
                    exc,
                    default_stage=ResearchExecutionErrorStage.VALIDATION,
                ),
            )

        prepared = replace(
            prepared,
            tool_call_id=tool_call_id,
            purpose=getattr(action, "purpose", None),
        )

        fingerprint = prepared.action_fingerprint
        with self._lock:
            cached = self._cache.get(fingerprint)
        if cached is None:
            cached = _restore_context_result(
                context,
                fingerprint=fingerprint,
                result_model=tool.result_model,
                replacement_tool_call_id=tool_call_id,
            )
            if cached is not None:
                with self._lock:
                    self._cache[fingerprint] = cached
        can_retry_same_parameters = bool(
            cached is not None
            and cached.status is ToolResultStatus.FAILED
            and cached.error is not None
            and cached.error.same_parameter_retryable
        )
        if cached is not None and not can_retry_same_parameters:
            # 相同动作重放只复用既有结果，不重新执行工具和扣减预算。
            return self._finalize_result(
                context,
                action,
                cached.model_copy(update={"tool_call_id": tool_call_id}),
                prepared=prepared,
            )

        budget_error = self._reserve_budget(prepared.cost, remaining_budget)
        if budget_error is not None:
            return self._finalize_result(
                context,
                action,
                self._failure(tool_call_id, action_type, budget_error),
                prepared=prepared,
            )

        return self._execute_prepared(
            action,
            tool_call_id,
            tool,
            prepared,
            fingerprint,
            context,
        )

    def execute_payload(
        self,
        payload: Mapping[str, Any],
        *,
        context: Any,
        tool_call_id: str,
        visible_tools: Collection[str | ResearchActionType] | None = None,
        run_status: ResearchStateStatus | str = ResearchStateStatus.RUNNING,
        remaining_budget: RemainingBudget | None = None,
        cancelled: Any = False,
    ) -> ToolResult[Any]:
        """解析并执行一个动作；解析失败也返回合法 ToolResult。"""

        action_name = _payload_action_type(payload)
        try:
            action = parse_research_action(payload)
        except ValidationError as exc:
            if action_name is None:
                raise ValueError("RESEARCH_ACTION_TYPE_INVALID") from exc
            result = self._persist(
                self._failure(
                    tool_call_id,
                    action_name,
                    exc,
                    default_stage=ResearchExecutionErrorStage.PARSING,
                )
            )
            _record_unparsed_context_attempt(
                context,
                action_name,
                payload,
                result,
                version_snapshot=self._version_snapshot,
            )
            return result
        return self.execute(
            action,
            context=context,
            tool_call_id=tool_call_id,
            visible_tools=visible_tools,
            run_status=run_status,
            remaining_budget=remaining_budget,
            cancelled=cancelled,
        )

    def execute_batch(
        self,
        actions: Sequence[tuple[str, ResearchAction]],
        *,
        context: Any,
        visible_tools: Collection[str | ResearchActionType] | None = None,
        run_status: ResearchStateStatus | str = ResearchStateStatus.RUNNING,
        remaining_budget: RemainingBudget | None = None,
        cancelled: Any = False,
    ) -> tuple[ToolResult[Any], ...]:
        """校验并执行一批动作；只有相互独立的只读动作允许并发。"""

        if not actions:
            raise ValueError("RESEARCH_ACTION_BATCH_EMPTY")
        call_ids = [call_id for call_id, _action in actions]
        if len(call_ids) != len(set(call_ids)):
            return tuple(
                self._finalize_result(
                    context,
                    action,
                    self._failure(
                        call_id,
                        action.action_type,
                        ResearchToolExecutionError(
                            ResearchToolExecutionError.ACTION_BATCH_INVALID,
                            "同一批动作不能使用重复的 tool_call_id",
                        ),
                    ),
                )
                for call_id, action in actions
            )

        fingerprints = [self.action_fingerprint(action) for _call_id, action in actions]
        if len(fingerprints) != len(set(fingerprints)):
            return tuple(
                self._finalize_result(
                    context,
                    action,
                    self._failure(
                        call_id,
                        action.action_type,
                        ResearchToolExecutionError(
                            ResearchToolExecutionError.ACTION_DUPLICATED,
                            "同一批动作包含等价的重复动作",
                        ),
                    ),
                )
                for call_id, action in actions
            )

        invalid_batch = [
            action
            for _call_id, action in actions
            if action.action_type.value not in _READ_ONLY_ACTIONS
            or not self._is_parallel_safe(action.action_type)
        ]
        if len(actions) > 1 and invalid_batch:
            return tuple(
                self._finalize_result(
                    context,
                    action,
                    self._failure(
                        call_id,
                        action.action_type,
                        ResearchToolExecutionError(
                            ResearchToolExecutionError.ACTION_BATCH_INVALID,
                            "并行批次只能包含相互独立且声明为并行安全的只读动作",
                            details={"action_type": action.action_type.value},
                        ),
                    ),
                )
                for call_id, action in actions
            )

        if len(actions) == 1:
            call_id, action = actions[0]
            return (
                self.execute(
                    action,
                    context=context,
                    tool_call_id=call_id,
                    visible_tools=visible_tools,
                    run_status=run_status,
                    remaining_budget=remaining_budget,
                    cancelled=cancelled,
                ),
            )

        with ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(actions))
        ) as pool:
            futures = [
                pool.submit(
                    self.execute,
                    action,
                    context=context,
                    tool_call_id=call_id,
                    visible_tools=visible_tools,
                    run_status=run_status,
                    remaining_budget=remaining_budget,
                    cancelled=cancelled,
                )
                for call_id, action in actions
            ]
            return tuple(future.result() for future in futures)

    def consumed_cost(self) -> ResearchToolCostEstimate:
        """返回当前 Runtime 已预留的工具成本。"""

        with self._lock:
            return self._consumed

    def action_fingerprint(self, action: ResearchAction) -> str:
        """按当前冻结版本生成动作指纹，供工具 prepare 复用。"""

        return research_action_fingerprint(
            action,
            version_snapshot=self._version_snapshot,
        )

    def _check_preconditions(
        self,
        action_type: ResearchActionType,
        tool_call_id: str,
        *,
        visible_tools: Collection[str | ResearchActionType] | None,
        run_status: ResearchStateStatus | str,
        cancelled: Any,
    ) -> ToolResult[Any] | None:
        if not _valid_call_id(tool_call_id):
            return self._failure(
                tool_call_id or "invalid-call",
                action_type,
                ValueError("RESEARCH_AGENT_TOOL_CALL_ID_INVALID"),
                default_stage=ResearchExecutionErrorStage.VALIDATION,
            )
        if _is_cancelled(cancelled):
            return self._failure(
                tool_call_id,
                action_type,
                ResearchToolExecutionError(
                    ResearchToolExecutionError.RUN_CANCELLED,
                    "研究 Run 已收到取消请求",
                ),
            )
        status = str(run_status)
        if status != ResearchStateStatus.RUNNING.value:
            code = (
                ResearchToolExecutionError.RUN_CANCELLED
                if status == ResearchStateStatus.CANCELLED.value
                else ResearchToolExecutionError.RUN_NOT_EXECUTABLE
            )
            return self._failure(
                tool_call_id,
                action_type,
                ResearchToolExecutionError(code, f"研究 Run 当前状态为 {status}"),
            )
        if visible_tools is not None and action_type.value not in {
            _name_value(item) for item in visible_tools
        }:
            return self._failure(
                tool_call_id,
                action_type,
                ResearchToolExecutionError(
                    ResearchToolExecutionError.TOOL_NOT_VISIBLE,
                    f"工具 {action_type.value} 不在当前可见工具集合中",
                ),
            )
        return None

    def _validate_prepared(self, prepared: Any) -> None:
        if not isinstance(prepared, PreparedResearchAction):
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "工具 prepare 未返回 PreparedResearchAction",
            )
        if (
            not isinstance(prepared.action_fingerprint, str)
            or not prepared.action_fingerprint.strip()
        ):
            raise ValueError("RESEARCH_ACTION_FINGERPRINT_REQUIRED")
        if not isinstance(prepared.cost, ResearchToolCostEstimate):
            raise ResearchToolExecutionError(
                ResearchToolExecutionError.PREPARE_FAILED,
                "工具 prepare 未返回有效成本估算",
            )

    def _reserve_budget(
        self,
        cost: ResearchToolCostEstimate,
        remaining: RemainingBudget | None,
    ) -> ResearchToolExecutionError | None:
        with self._lock:
            projected = _add_cost(self._consumed, cost)
            if remaining is not None:
                if projected.query_calls > remaining.query_calls:
                    return ResearchToolExecutionError(
                        ResearchToolExecutionError.BUDGET_EXHAUSTED,
                        "查询预算不足",
                        details={
                            "required": cost.query_calls,
                            "remaining": remaining.query_calls,
                        },
                    )
                if projected.compute_calls > remaining.compute_calls:
                    return ResearchToolExecutionError(
                        ResearchToolExecutionError.BUDGET_EXHAUSTED,
                        "计算预算不足",
                        details={
                            "required": cost.compute_calls,
                            "remaining": remaining.compute_calls,
                        },
                    )
                if projected.semantic_search_calls > remaining.semantic_search_calls:
                    return ResearchToolExecutionError(
                        ResearchToolExecutionError.BUDGET_EXHAUSTED,
                        "语义检索预算不足",
                        details={
                            "required": cost.semantic_search_calls,
                            "remaining": remaining.semantic_search_calls,
                        },
                    )
                if projected.wall_time_ms > remaining.wall_time_ms:
                    return ResearchToolExecutionError(
                        ResearchToolExecutionError.BUDGET_EXHAUSTED,
                        "执行时间预算不足",
                        details={
                            "required": cost.wall_time_ms,
                            "remaining": remaining.wall_time_ms,
                        },
                    )
                if projected.query_cost > remaining.query_cost:
                    return ResearchToolExecutionError(
                        ResearchToolExecutionError.BUDGET_EXHAUSTED,
                        "查询成本预算不足",
                        details={
                            "required": cost.query_cost,
                            "remaining": remaining.query_cost,
                        },
                    )
            self._consumed = projected
        return None

    def _execute_prepared(
        self,
        action: ResearchAction,
        tool_call_id: str,
        tool: ResearchTool[Any, Any, Any],
        prepared: PreparedResearchAction[Any, Any],
        fingerprint: str,
        context: Any,
    ) -> ToolResult[Any]:
        action_type = action.action_type
        try:
            payload = tool.execute(context, prepared)
            inject_failure = getattr(context, "inject_failure", None)
            if callable(inject_failure):
                inject_failure("after_tool_execute")
        except (
            ResearchToolExecutionError,
            ValidationError,
            PermissionError,
            TimeoutError,
            ValueError,
        ) as exc:
            result = self._failure(
                tool_call_id,
                action_type,
                exc,
                default_stage=ResearchExecutionErrorStage.EXECUTION,
            )
        else:
            result_model = cast(type[BaseModel], tool.result_model)
            try:
                result_payload = result_model.model_validate(payload)
            except ValidationError as exc:
                result = self._failure(
                    tool_call_id,
                    action_type,
                    ResearchToolExecutionError(
                        ResearchToolExecutionError.RESULT_INVALID,
                        f"工具 {action_type.value} 返回的结果不符合契约：{exc}",
                    ),
                )
            else:
                if action_type is ResearchActionType.REQUEST_CLARIFICATION:
                    result = self._waiting_for_user(
                        tool_call_id,
                        action_type,
                        result_payload,
                    )
                else:
                    result = self._success(tool_call_id, action_type, result_payload)

        result = self._finalize_result(
            context,
            action,
            result,
            prepared=prepared,
            consume_cost=True,
        )

        with self._lock:
            self._cache[fingerprint] = result
        return result

    def _finalize_result(
        self,
        context: Any,
        action: ResearchAction,
        result: ToolResult[Any],
        *,
        prepared: PreparedResearchAction[Any, Any] | None = None,
        consume_cost: bool = False,
    ) -> ToolResult[Any]:
        """统一持久化结果，并记录本次动作的 Attempt 事实。"""

        result = self._persist(result)
        # 并行动作完成时间不同；用 Runtime 锁串行写入共享状态，避免预算和
        # AttemptSummary 的更新互相覆盖。
        with self._lock:
            _record_context_facts(
                context,
                action,
                prepared,
                result,
                consume_cost=consume_cost,
                action_fingerprint=(
                    prepared.action_fingerprint
                    if prepared is not None
                    else self.action_fingerprint(action)
                ),
            )
        return result

    def _is_parallel_safe(self, action_type: ResearchActionType) -> bool:
        tool = self._registry.get(action_type)
        return bool(tool is not None and getattr(tool, "parallel_safe", False))

    def _persist(self, result: ToolResult[Any]) -> ToolResult[Any]:
        """把成功和失败结果交给同一个持久化回调。"""

        if self._persist_result is None:
            return result
        try:
            self._persist_result(result)
        except ResearchToolExecutionError as exc:
            # 持久化失败本身不能再次递归持久化；返回明确的失败结果。
            return self._failure(
                result.tool_call_id,
                result.name,
                exc,
                default_stage=ResearchExecutionErrorStage.PERSISTENCE,
            )
        except (ValidationError, PermissionError, TimeoutError, ValueError) as exc:
            return self._failure(
                result.tool_call_id,
                result.name,
                ResearchToolExecutionError(
                    ResearchToolExecutionError.PERSISTENCE_FAILED,
                    str(exc) or "ToolResult 持久化失败",
                    retryable=True,
                ),
                default_stage=ResearchExecutionErrorStage.PERSISTENCE,
            )
        return result

    @staticmethod
    def _success(
        tool_call_id: str,
        action_type: ResearchActionType,
        payload: BaseModel,
    ) -> ToolResult[Any]:
        return ToolResult(
            tool_call_id=tool_call_id,
            name=action_type,
            status=ToolResultStatus.SUCCEEDED,
            result=payload,
        )

    @staticmethod
    def _waiting_for_user(
        tool_call_id: str,
        action_type: ResearchActionType,
        payload: BaseModel,
    ) -> ToolResult[Any]:
        """澄清工具成功创建请求，但 Run 需要等待用户回答。"""

        return ToolResult(
            tool_call_id=tool_call_id,
            name=action_type,
            status=ToolResultStatus.WAITING_FOR_USER,
            result=payload,
        )

    @staticmethod
    def _failure(
        tool_call_id: str,
        action_type: ResearchActionType,
        error: Exception,
        *,
        default_stage: ResearchExecutionErrorStage = ResearchExecutionErrorStage.EXECUTION,
    ) -> ToolResult[Any]:
        try:
            mapped = map_research_tool_error(error, default_stage=default_stage)
        except TypeError:
            raise
        return ToolResult(
            tool_call_id=tool_call_id,
            name=action_type,
            status=ToolResultStatus.FAILED,
            error=mapped,
        )


def parse_research_action(payload: Mapping[str, Any]) -> ResearchAction:
    """按 ``action_type`` 解析单个 ResearchAction 联合类型。"""

    action_type = _payload_action_type(payload)
    if action_type is None:
        raise ValidationError.from_exception_data(
            "ResearchAction",
            [{"type": "missing", "loc": ("action_type",), "input": payload}],
        )
    model = cast(Any, _ACTION_MODELS[action_type.value])
    return cast(ResearchAction, model.model_validate(payload))


def parse_research_turn(payload: Mapping[str, Any]) -> ResearchTurnDecision:
    """解析完整轮次，统一使用阶段 1 定义的联合校验。"""

    return ResearchTurnDecision.model_validate(payload)


def _tool_name(tool: ResearchTool[Any, Any, Any]) -> str:
    return _name_value(tool.name)


def _name_value(value: str | ResearchActionType) -> str:
    return value.value if isinstance(value, ResearchActionType) else str(value)


def _record_context_facts(
    context: Any,
    action: ResearchAction,
    prepared: PreparedResearchAction[Any, Any] | None,
    result: ToolResult[Any],
    *,
    consume_cost: bool = False,
    action_fingerprint: str | None = None,
) -> None:
    """按 ToolResult、Attempt、预算的顺序写入阶段 6事实。"""

    record_attempt = getattr(context, "record_research_attempt", None)
    if callable(record_attempt):
        record_attempt(
            _attempt_from_result(
                action,
                prepared,
                result,
                action_fingerprint=action_fingerprint,
            )
        )
    consume_cost_fn = getattr(context, "consume_research_cost", None)
    if consume_cost and prepared is not None and callable(consume_cost_fn):
        consume_cost_fn(prepared.cost)
    remember_fingerprint = getattr(context, "remember_fingerprint", None)
    if callable(remember_fingerprint) and action_fingerprint:
        evidence_id = getattr(result.result, "evidence_id", None)
        result_id = (
            context.research_evidence_result_id(evidence_id)
            if isinstance(evidence_id, str)
            and callable(getattr(context, "research_evidence_result_id", None))
            else None
        )
        if result.status in {
            ToolResultStatus.SUCCEEDED,
            ToolResultStatus.WAITING_FOR_USER,
        }:
            remember_fingerprint(
                action_fingerprint,
                evidence_id=evidence_id if isinstance(evidence_id, str) else None,
                result_id=result_id,
            )


def _restore_context_result(
    context: Any,
    *,
    fingerprint: str,
    result_model: type[BaseModel],
    replacement_tool_call_id: str,
) -> ToolResult[Any] | None:
    """从已持久化的 Attempt 恢复同指纹成功结果，避免恢复后重复执行。"""

    attempts = getattr(context, "react_attempts", None)
    load_result = getattr(context, "research_tool_result", None)
    if not callable(attempts) or not callable(load_result):
        return None
    for attempt in reversed(attempts()):
        if attempt.action_fingerprint != fingerprint:
            continue
        if attempt.status not in {"succeeded", "waiting_for_user"}:
            return None
        original_call_id = attempt.attempt_id.removeprefix("attempt:")
        payload = load_result(original_call_id)
        if payload is None:
            return None
        restored = restore_research_tool_result(payload, result_model)
        return restored.model_copy(update={"tool_call_id": replacement_tool_call_id})
    return None


def _attempt_from_result(
    action: ResearchAction,
    prepared: PreparedResearchAction[Any, Any] | None,
    result: ToolResult[Any],
    *,
    action_fingerprint: str | None = None,
) -> AttemptSummary:
    """把 ToolResult 转为可供 finish_research 引用的 AttemptSummary。"""

    status = result.status.value
    error = result.error
    if (
        result.status is ToolResultStatus.SUCCEEDED
        and getattr(result.result, "decision", None) == "rejected"
    ):
        status = "rejected"
        error = ExecutionError(
            code="RESEARCH_AGENT_COMPLETION_REJECTED",
            stage=ResearchExecutionErrorStage.COMPLETION,
            message=str(getattr(result.result, "message", "结束请求被拒绝")),
        )
    produced_evidence_ids: tuple[str, ...] = ()
    evidence_id = getattr(result.result, "evidence_id", None)
    if isinstance(evidence_id, str) and evidence_id.startswith("evidence:"):
        produced_evidence_ids = (evidence_id,)
    parameter_summary = json.dumps(
        (
            prepared.args.model_dump(mode="json")
            if prepared is not None
            else action.arguments.model_dump(mode="json")
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:2_000]
    return AttemptSummary(
        attempt_id=f"attempt:{result.tool_call_id}",
        action_type=action.action_type,
        purpose=(prepared.purpose if prepared is not None else None) or action.purpose,
        parameter_summary=parameter_summary,
        action_fingerprint=action_fingerprint
        or (
            prepared.action_fingerprint
            if prepared is not None
            else research_action_fingerprint(action)
        ),
        status=cast(Any, status),
        produced_evidence_ids=produced_evidence_ids,
        error=error,
    )


def _record_unparsed_context_attempt(
    context: Any,
    action_type: ResearchActionType,
    payload: Mapping[str, Any],
    result: ToolResult[Any],
    *,
    version_snapshot: Any,
) -> None:
    """为已识别动作类型但参数解析失败的调用保存摘要。"""

    record_attempt = getattr(context, "record_research_attempt", None)
    if not callable(record_attempt):
        return
    purpose = payload.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        purpose = "动作解析"
    arguments = payload.get("arguments")
    parameter_payload = arguments if isinstance(arguments, Mapping) else payload
    encoded = json.dumps(
        parameter_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if isinstance(arguments, Mapping):
        fingerprint = research_action_fingerprint(
            payload,
            version_snapshot=version_snapshot,
        )
    else:
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
        fingerprint = f"{action_type.value}_{digest}"
    record_attempt(
        AttemptSummary(
            attempt_id=f"attempt:{result.tool_call_id}",
            action_type=action_type,
            purpose=purpose,
            parameter_summary=encoded[:2_000],
            action_fingerprint=fingerprint,
            status="failed",
            error=result.error,
        )
    )


def _payload_action_type(
    payload: Mapping[str, Any],
) -> ResearchActionType | None:
    value = payload.get("action_type")
    if not isinstance(value, str):
        return None
    try:
        return ResearchActionType(value)
    except ValueError:
        return None


def _valid_call_id(value: str) -> bool:
    import re

    return bool(re.fullmatch(_CALL_ID_PATTERN, value))


def _is_cancelled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    checker = getattr(value, "is_cancelled", None)
    return bool(checker()) if callable(checker) else False


def _add_cost(
    left: ResearchToolCostEstimate,
    right: ResearchToolCostEstimate,
) -> ResearchToolCostEstimate:
    return replace(
        left,
        query_calls=left.query_calls + right.query_calls,
        compute_calls=left.compute_calls + right.compute_calls,
        semantic_search_calls=left.semantic_search_calls + right.semantic_search_calls,
        wall_time_ms=left.wall_time_ms + right.wall_time_ms,
        query_cost=left.query_cost + right.query_cost,
    )


__all__ = [
    "ResearchToolRegistry",
    "ResearchToolRuntime",
    "parse_research_action",
    "parse_research_turn",
]
