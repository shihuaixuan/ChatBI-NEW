"""ChatBI 澄清控制 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.chatbi.services.planning.semantic_clarification import (
    ambiguous_candidates as _ambiguous_candidates,
)
from apps.chatbi.services.planning.semantic_clarification import (
    asset_key as _asset_key,
)
from apps.chatbi.services.planning.semantic_clarification import (
    build_semantic_clarification,
)
from apps.chatbi.services.planning.semantic_clarification import (
    executable_asset_type as _executable_asset_type,
)
from apps.chatbi.services.planning.semantic_clarification import (
    normalize_option_value as _normalize_option_value,
)
from apps.chatbi.services.planning.semantic_clarification import (
    semantic_candidate_names as _semantic_candidate_names,
)
from apps.retrieval import (
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalQueryError,
    SemanticClarificationBinding,
    apply_semantic_clarification,
)
from apps.tool import (
    ToolErrorCategory,
    ToolExecutionPolicy,
    ToolResult,
)


class ClarifyOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="展示给用户的选项文案")
    value: str = Field(description="选项值")
    asset_id: int | None = Field(default=None, description="选项对应的语义资产 id（如指标候选）")
    bindings: list[SemanticClarificationBinding] = Field(
        default_factory=list,
        description="选项对应的语义槽位绑定；一个选项可以同时确认指标和维度",
    )


class ClarifyArgs(BaseModel):
    """向用户澄清关键歧义。这是终止动作：本轮暂停，等待用户回答后继续。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, description="向用户提出的澄清问题")
    options: list[ClarifyOption] = Field(
        default_factory=list,
        description="结构化选项（强烈建议提供，来自语义包候选）；为空表示自由文本澄清",
    )


class ClarifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    pending_clarifications: list[dict[str, Any]] = Field(default_factory=list)


def prepare_semantic_clarification_args(
    state: dict[str, Any],
) -> ClarifyArgs | None:
    """把服务层生成的结构化选项适配成 Tool 参数模型。"""

    clarification = build_semantic_clarification(state)
    if clarification is None:
        return None
    return ClarifyArgs(
        question=clarification.question,
        options=[
            ClarifyOption.model_validate(item.model_dump(mode="python"))
            for item in clarification.options
        ],
    )


class ClarifyTool(AgentTool):
    """clarify 是终止动作：execute 只做结构校验，挂起和持久化由 Agent 编排层处理。"""

    name = "clarify"
    description = (
        "当歧义会影响 SQL 正确性时（指标口径二义、时间范围缺失、维度不明确），向用户澄清。"
        "必须给出来自语义包候选的结构化选项。每个语义资产选项应填写 asset_id，或让 value "
        "精确等于候选的 display_name、biz_name、asset_id；服务端会补全并校验 bindings。"
    )
    args_model = ClarifyArgs
    result_model = ClarifyResult
    execution = ToolExecutionPolicy(timeout_seconds=5)

    def execute(
        self,
        ctx: AgentToolContext,
        args: ClarifyArgs,
    ) -> ToolResult[ClarifyResult]:
        scope = ctx.semantic_asset_scope
        if scope is not None and scope.decision_status == RetrievalDecisionStatus.AMBIGUOUS:
            if not args.options:
                return ToolResult.rejected(
                    "当前语义决策存在歧义，必须提供来自原候选的结构化选项。",
                    error_code="semantic_clarification_options_required",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                )
            raw_bundle = ctx.state.get("semantic_bundle")
            if not isinstance(raw_bundle, dict):
                return ToolResult.rejected(
                    "缺少服务端语义决策快照，无法创建可验证的澄清选项。",
                    error_code="semantic_clarification_snapshot_required",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                )
            bundle = RetrievalBundle.model_validate(raw_bundle)
            required_subquery_ids = _required_subquery_ids(ctx.state)
            try:
                options = _canonicalize_semantic_options(
                    bundle,
                    ctx.state,
                    args.options,
                )
            except RetrievalQueryError as exc:
                return ToolResult.rejected(
                    str(exc),
                    error_code="semantic_clarification_option_invalid",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                    details=exc.details,
                )
            for option in options:
                try:
                    apply_semantic_clarification(
                        bundle,
                        option.bindings,
                        required_subquery_ids=required_subquery_ids,
                    )
                except RetrievalQueryError as exc:
                    return ToolResult.rejected(
                        str(exc),
                        error_code="semantic_clarification_option_invalid",
                        error_category=ToolErrorCategory.BUSINESS_RULE,
                        details=exc.details,
                    )
        else:
            options = args.options
        pending_clarifications: list[dict[str, Any]] = []
        if ctx.state.get("time_parse_status") == "unsupported" and any(
            option.bindings for option in options
        ):
            # 当前卡片先处理模型已识别的业务歧义，时间卡片在用户回答后继续展示。
            pending_clarifications.append(
                {
                    "question": "当前时间表达暂不支持，请提供明确的起止日期。",
                    "options": [],
                    "tool_call_id": None,
                    "resume_kind": "question_understanding",
                    "resume_payload": {"operation": "resolve_time_range"},
                }
            )
        return ToolResult.succeeded(
            "clarify",
            ClarifyResult(
                question=args.question,
                options=[option.model_dump() for option in options],
                pending_clarifications=pending_clarifications,
            ),
        )


def _required_subquery_ids(state: dict[str, Any]) -> set[str] | None:
    filters = state.get("semantic_retrieval_filters")
    if not isinstance(filters, dict):
        return None
    subqueries = filters.get("subqueries")
    if not isinstance(subqueries, list):
        return None
    return {
        str(item["subquery_id"])
        for item in subqueries
        if isinstance(item, dict)
        and item.get("required") is True
        and item.get("subquery_id")
    }


def _canonicalize_semantic_options(
    bundle: RetrievalBundle,
    state: dict[str, Any],
    options: list[ClarifyOption],
) -> list[ClarifyOption]:
    """只依据服务端原始歧义候选补全或修正模型提交的绑定。"""

    candidate_names = _semantic_candidate_names(state)
    canonical: list[ClarifyOption] = []
    for option in options:
        bindings = (
            [
                _canonical_binding(
                    bundle,
                    asset_type=binding.asset_type.value,
                    asset_id=binding.asset_id,
                    model_id=binding.model_id,
                    proposed_subquery_id=binding.subquery_id,
                )
                for binding in option.bindings
            ]
            if option.bindings
            else [
                _binding_from_option(
                    bundle,
                    option,
                    candidate_names,
                )
            ]
        )
        canonical.append(option.model_copy(update={"bindings": bindings}))
    return canonical


def _binding_from_option(
    bundle: RetrievalBundle,
    option: ClarifyOption,
    candidate_names: dict[tuple[str, int, int | None], set[str]],
) -> SemanticClarificationBinding:
    candidates = _ambiguous_candidates(bundle)
    if option.asset_id is not None:
        matches = [
            (subquery_id, asset)
            for subquery_id, asset in candidates
            if asset.asset_id == option.asset_id
        ]
    else:
        value = _normalize_option_value(option.value)
        matches = [
            (subquery_id, asset)
            for subquery_id, asset in candidates
            if value in candidate_names.get(_asset_key(asset), set())
        ]
    if len(matches) != 1:
        raise RetrievalQueryError(
            "澄清选项无法唯一匹配原始语义候选",
            details={
                "reason_code": "SEMANTIC_CLARIFICATION_OPTION_NOT_UNIQUE",
                "option_value": option.value,
                "asset_id": option.asset_id,
            },
        )
    subquery_id, asset = matches[0]
    return SemanticClarificationBinding(
        subquery_id=subquery_id,
        asset_type=_executable_asset_type(asset),
        asset_id=asset.asset_id,
        model_id=asset.model_id,
    )


def _canonical_binding(
    bundle: RetrievalBundle,
    *,
    asset_type: str,
    asset_id: int,
    model_id: int | None,
    proposed_subquery_id: str,
) -> SemanticClarificationBinding:
    candidates = [
        (subquery_id, asset)
        for subquery_id, asset in _ambiguous_candidates(bundle)
        if asset.asset_type.value == asset_type
        and asset.asset_id == asset_id
        and (model_id is None or asset.model_id == model_id)
    ]
    exact_slot = [
        item for item in candidates if item[0] == proposed_subquery_id
    ]
    matches = exact_slot or candidates
    if len(matches) != 1:
        raise RetrievalQueryError(
            "澄清绑定无法唯一匹配原始语义候选",
            details={
                "reason_code": "SEMANTIC_CLARIFICATION_BINDING_NOT_UNIQUE",
                "asset_type": asset_type,
                "asset_id": asset_id,
                "model_id": model_id,
                "proposed_subquery_id": proposed_subquery_id,
            },
        )
    subquery_id, asset = matches[0]
    return SemanticClarificationBinding(
        subquery_id=subquery_id,
        asset_type=_executable_asset_type(asset),
        asset_id=asset.asset_id,
        model_id=asset.model_id,
    )
