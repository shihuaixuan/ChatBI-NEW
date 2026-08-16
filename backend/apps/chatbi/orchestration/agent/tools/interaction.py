"""ChatBI 澄清控制 Tool。"""

from __future__ import annotations

from itertools import product
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.retrieval import (
    AssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalPurpose,
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
    pending_clarifications: list[dict[str, Any]] = Field(default_factory=list)


def prepare_semantic_clarification_args(
    state: dict[str, Any],
) -> ClarifyArgs | None:
    """从服务端权威歧义快照生成澄清参数，避免模型自行拼装资产绑定。"""

    raw_bundle = state.get("semantic_bundle")
    if not isinstance(raw_bundle, dict):
        return None
    bundle = RetrievalBundle.model_validate(raw_bundle)
    if bundle.decision.status != RetrievalDecisionStatus.AMBIGUOUS:
        return None

    candidate_details = _semantic_candidate_details(state)
    options: list[ClarifyOption] = []
    ambiguity_names: list[str] = []
    ambiguities_by_purpose = {
        purpose: [
            ambiguity
            for ambiguity in bundle.decision.ambiguities
            if _ambiguity_purpose(bundle, ambiguity.subquery_id) == purpose
        ]
        for purpose in (RetrievalPurpose.METRIC, RetrievalPurpose.DIMENSION)
    }
    consumed_subquery_ids: set[str] = set()
    metric_ambiguities = ambiguities_by_purpose[RetrievalPurpose.METRIC]
    dimension_ambiguities = ambiguities_by_purpose[RetrievalPurpose.DIMENSION]
    if len(metric_ambiguities) == 1 and dimension_ambiguities:
        metric_ambiguity = metric_ambiguities[0]
        for metric in metric_ambiguity.candidate_assets:
            compatible_by_dimension_slot = [
                [
                    dimension
                    for dimension in ambiguity.candidate_assets
                    if _metric_dimension_compatible(bundle, metric, dimension)
                ]
                for ambiguity in dimension_ambiguities
            ]
            if any(not candidates for candidates in compatible_by_dimension_slot):
                continue
            for dimensions in product(*compatible_by_dimension_slot):
                metric_name = _candidate_display_name(candidate_details, metric)
                if metric_name not in ambiguity_names:
                    ambiguity_names.append(metric_name)
                bindings = [
                    SemanticClarificationBinding(
                        subquery_id=metric_ambiguity.subquery_id,
                        asset_type=RetrievalResourceType.METRIC,
                        asset_id=metric.asset_id,
                        model_id=metric.model_id,
                    ),
                    *[
                        SemanticClarificationBinding(
                            subquery_id=ambiguity.subquery_id,
                            asset_type=RetrievalResourceType.DIMENSION,
                            asset_id=dimension.asset_id,
                            model_id=dimension.model_id,
                        )
                        for ambiguity, dimension in zip(
                            dimension_ambiguities,
                            dimensions,
                            strict=True,
                        )
                    ],
                ]
                dimension_names = [
                    _candidate_display_name(candidate_details, dimension)
                    for dimension in dimensions
                ]
                label = metric_name
                if len(compatible_by_dimension_slot) > 1 or any(
                    len(candidates) > 1 for candidates in compatible_by_dimension_slot
                ):
                    label = f"{metric_name}（{'、'.join(dimension_names)}）"
                options.append(
                    ClarifyOption(
                        label=label,
                        value="|".join(
                            f"{binding.asset_type.value}:{binding.asset_id}:"
                            f"{binding.model_id or 0}"
                            for binding in bindings
                        ),
                        asset_id=metric.asset_id,
                        bindings=bindings,
                    )
                )
        if options:
            consumed_subquery_ids = {
                metric_ambiguity.subquery_id,
                *(item.subquery_id for item in dimension_ambiguities),
            }

    for ambiguity in bundle.decision.ambiguities:
        if ambiguity.subquery_id in consumed_subquery_ids:
            continue
        base_names = [
            _candidate_display_name(candidate_details, asset)
            for asset in ambiguity.candidate_assets
        ]
        duplicate_names = {
            name for name in base_names if base_names.count(name) > 1
        }
        for index, asset in enumerate(ambiguity.candidate_assets, start=1):
            name = _candidate_display_name(candidate_details, asset)
            if name not in ambiguity_names:
                ambiguity_names.append(name)
            label = name
            if name in duplicate_names:
                biz_name = str(
                    candidate_details.get(_asset_key(asset), {}).get("biz_name") or ""
                ).strip()
                label = f"{name}（{biz_name or index}）"
            options.append(
                ClarifyOption(
                    label=label,
                    value=(
                        f"{asset.asset_type.value}:{asset.asset_id}:"
                        f"{asset.model_id or 0}"
                    ),
                    asset_id=asset.asset_id,
                    bindings=[
                        SemanticClarificationBinding(
                            subquery_id=ambiguity.subquery_id,
                            asset_type=_executable_asset_type(asset),
                            asset_id=asset.asset_id,
                            model_id=asset.model_id,
                        )
                    ],
                )
            )
    if not options:
        return None

    subject = "、".join(ambiguity_names) or "当前查询"
    return ClarifyArgs(
        question=f"“{subject}”存在多个可执行口径，请选择本次要查询的口径。",
        options=options,
    )


def _ambiguity_purpose(
    bundle: RetrievalBundle,
    subquery_id: str,
) -> RetrievalPurpose | None:
    return next(
        (
            decision.purpose
            for decision in bundle.decision.slot_decisions
            if decision.subquery_id == subquery_id
        ),
        None,
    )


def _metric_dimension_compatible(
    bundle: RetrievalBundle,
    metric: AssetReference,
    dimension: AssetReference,
) -> bool:
    if metric.model_id is not None and metric.model_id == dimension.model_id:
        return True
    metric_hit = next(
        (
            hit
            for hit in bundle.bindings.metrics
            if hit.asset_ref is not None and _asset_key(hit.asset_ref) == _asset_key(metric)
        ),
        None,
    )
    if metric_hit is None:
        return False
    compatible_dimension_ids = {
        int(value)
        for value in metric_hit.metadata.get("compatible_dimension_ids", [])
        if isinstance(value, int) and value > 0
    }
    return dimension.asset_id in compatible_dimension_ids


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
    return {
        key: {
            normalized
            for value in (
                item.get("display_name"),
                item.get("name"),
                item.get("biz_name"),
                str(key[1]),
            )
            if (normalized := _normalize_option_value(value))
        }
        for key, item in _semantic_candidate_details(state).items()
    }


def _semantic_candidate_details(
    state: dict[str, Any],
) -> dict[tuple[str, int, int | None], dict[str, Any]]:
    payload = state.get("semantic_payload")
    if not isinstance(payload, dict):
        payload = state.get("semantic_package")
    groups = payload.get("candidate_groups") if isinstance(payload, dict) else None
    result: dict[tuple[str, int, int | None], dict[str, Any]] = {}
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
            result[key] = item
    return result


def _candidate_display_name(
    candidate_details: dict[tuple[str, int, int | None], dict[str, Any]],
    asset: AssetReference,
) -> str:
    details = candidate_details.get(_asset_key(asset), {})
    if str(details.get("asset_type") or "") == "VALUE":
        # 维值澄清选项直接展示维值本身，而不是所属维度名。
        value_name = str(details.get("retrieval_title") or "").strip()
        if value_name:
            return value_name
    return str(
        details.get("display_name")
        or details.get("name")
        or details.get("biz_name")
        or details.get("retrieval_title")
        or f"业务口径 {asset.asset_id}"
    )


def _asset_key(asset: AssetReference) -> tuple[str, int, int | None]:
    return asset.asset_type.value, asset.asset_id, asset.model_id


def _executable_asset_type(
    asset: AssetReference,
) -> Literal[
    RetrievalResourceType.METRIC,
    RetrievalResourceType.DIMENSION,
    RetrievalResourceType.VALUE,
]:
    if asset.asset_type == RetrievalResourceType.METRIC:
        return RetrievalResourceType.METRIC
    if asset.asset_type == RetrievalResourceType.DIMENSION:
        return RetrievalResourceType.DIMENSION
    if asset.asset_type == RetrievalResourceType.VALUE:
        # VALUE 候选的 asset_id 指向维值所属维度；选中后由 payload 还原 canonical 值。
        return RetrievalResourceType.VALUE
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
