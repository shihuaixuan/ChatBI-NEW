"""ChatBI 澄清控制 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.retrieval import (
    AssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalQueryError,
    RetrievalResourceType,
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
        return ToolResult.succeeded(
            "clarify",
            ClarifyResult(
                question=args.question,
                options=[option.model_dump() for option in options],
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


def _ambiguous_candidates(
    bundle: RetrievalBundle,
) -> list[tuple[str, AssetReference]]:
    return [
        (ambiguity.subquery_id, asset)
        for ambiguity in bundle.decision.ambiguities
        for asset in ambiguity.candidate_assets
    ]


def _semantic_candidate_names(
    state: dict[str, Any],
) -> dict[tuple[str, int, int | None], set[str]]:
    payload = state.get("semantic_payload")
    if not isinstance(payload, dict):
        payload = state.get("semantic_package")
    groups = payload.get("candidate_groups") if isinstance(payload, dict) else None
    result: dict[tuple[str, int, int | None], set[str]] = {}
    if not isinstance(groups, dict):
        return result
    for items in groups.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            asset_type = str(item.get("asset_type") or "")
            asset_id = item.get("asset_id")
            model_id = item.get("model_id")
            if not isinstance(asset_id, int):
                continue
            key = (
                asset_type,
                asset_id,
                model_id if isinstance(model_id, int) else None,
            )
            result[key] = {
                normalized
                for value in (
                    item.get("display_name"),
                    item.get("name"),
                    item.get("biz_name"),
                    str(asset_id),
                )
                if (normalized := _normalize_option_value(value))
            }
    return result


def _asset_key(asset: AssetReference) -> tuple[str, int, int | None]:
    return asset.asset_type.value, asset.asset_id, asset.model_id


def _executable_asset_type(
    asset: AssetReference,
) -> Literal[RetrievalResourceType.METRIC, RetrievalResourceType.DIMENSION]:
    if asset.asset_type == RetrievalResourceType.METRIC:
        return RetrievalResourceType.METRIC
    if asset.asset_type == RetrievalResourceType.DIMENSION:
        return RetrievalResourceType.DIMENSION
    raise RetrievalQueryError(
        "语义澄清候选不是可执行资产",
        details={
            "reason_code": "SEMANTIC_CLARIFICATION_ASSET_TYPE_NOT_EXECUTABLE",
            "asset_type": asset.asset_type.value,
            "asset_id": asset.asset_id,
        },
    )


def _normalize_option_value(value: Any) -> str:
    return "".join(str(value or "").strip().casefold().split())
