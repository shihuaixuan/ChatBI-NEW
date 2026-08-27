"""候选资产后的语义解析模型调用与确定性校验。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.chatbi.errors import QuestionUnderstandingError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.services.understanding.model_invocation import StructuredModelService


class SemanticParseCandidate(BaseModel):
    """提供给语义解析模型的最小候选资产信息。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1)
    asset_type: str = Field(min_length=1)
    asset_id: int = Field(gt=0)
    model_id: int | None = Field(default=None, gt=0)
    display_name: str = Field(min_length=1)
    biz_name: str = Field(min_length=1)
    description: str = ""
    matched_phrases: list[str] = Field(default_factory=list)
    score: float | None = None


class SemanticParseCandidateContext(BaseModel):
    """语义解析模型的输入上下文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rewrite_question: str = Field(min_length=1)
    candidate_groups: dict[str, list[SemanticParseCandidate]]


class SemanticParseService:
    """根据重写问题和候选资产生成语义解析 JSON。"""

    def __init__(self, model_service: StructuredModelService) -> None:
        if model_service is None:
            raise ValueError("SEMANTIC_PARSE_MODEL_SERVICE_REQUIRED")
        self._model_service = model_service

    def parse(
        self,
        *,
        rewrite_question: str,
        candidate_payload: dict[str, Any],
    ) -> SemanticParseOutput:
        """调用模型并校验结果只能引用当前候选资产。"""

        context = self._build_context(rewrite_question, candidate_payload)
        invocation = QuestionModelInvocationData(
            stage="semantic_parse",
            system_prompt=SEMANTIC_PARSE_SYSTEM_PROMPT,
            user_prompt=build_semantic_parse_user_prompt(context),
            json_mode=QuestionModelJSONMode.STRICT,
        )
        result = self._model_service.invoke(invocation)
        try:
            return self._validate_output(result.payload, context)
        except QuestionUnderstandingError as first_exc:
            repaired = self._model_service.invoke(
                QuestionModelInvocationData(
                    stage=invocation.stage,
                    system_prompt=invocation.system_prompt,
                    user_prompt=(
                        invocation.user_prompt
                        + "\n\n上一次输出未通过语义解析契约校验。"
                        + f"具体错误：{first_exc}。"
                        + "请修正后重新输出完整 JSON；不得删除用户明确提到的指标、"
                        + "维度、时间或计算要求。"
                    ),
                    json_mode=invocation.json_mode,
                )
            )
            return self._validate_output(repaired.payload, context)

    @classmethod
    def _validate_output(
        cls,
        payload: dict[str, Any],
        context: SemanticParseCandidateContext,
    ) -> SemanticParseOutput:
        """统一执行 DTO、候选范围和计算输入完整性校验。"""

        try:
            output = SemanticParseOutput.model_validate(payload)
        except ValidationError as exc:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_OUTPUT_INVALID",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        cls._validate_candidate_refs(output, context)
        cls._validate_calculation_inputs(output)
        return output

    @staticmethod
    def _validate_calculation_inputs(output: SemanticParseOutput) -> None:
        """需要维度输入的计算不能在维度丢失后继续进入执行计划。"""

        contribution_requested = any(
            item.calculation is not None and item.calculation.value == "contribution"
            for item in output.calculation_operations()
        )
        if not contribution_requested:
            return
        dimension_refs = set(output.dimension_group_refs())
        if (
            output.multi_step is not None
            and output.multi_step.type == "dynamic_research"
        ):
            dimension_refs.update(output.multi_step.required_dimension_refs)
        if not dimension_refs:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_CONTRIBUTION_DIMENSION_REQUIRED"
            )

    @staticmethod
    def _build_context(
        rewrite_question: str,
        candidate_payload: dict[str, Any],
    ) -> SemanticParseCandidateContext:
        question = str(rewrite_question or "").strip()
        if not question:
            raise QuestionUnderstandingError("SEMANTIC_PARSE_REWRITE_QUESTION_REQUIRED")
        raw_groups = candidate_payload.get("candidate_groups")
        if not isinstance(raw_groups, dict):
            raise QuestionUnderstandingError("SEMANTIC_PARSE_CANDIDATES_REQUIRED")

        groups: dict[str, list[SemanticParseCandidate]] = {
            "metrics": [],
            "dimensions": [],
        }
        for group_name in groups:
            raw_items = raw_groups.get(group_name) or []
            if not isinstance(raw_items, list):
                raise QuestionUnderstandingError(
                    "SEMANTIC_PARSE_CANDIDATES_INVALID",
                    details={"group": group_name},
                )
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    raise QuestionUnderstandingError(
                        "SEMANTIC_PARSE_CANDIDATE_INVALID",
                        details={"group": group_name},
                    )
                candidate_payload = {
                    "ref": raw_item.get("ref"),
                    "asset_type": raw_item.get("asset_type"),
                    "asset_id": raw_item.get("asset_id"),
                    "model_id": raw_item.get("model_id"),
                    "display_name": raw_item.get("display_name")
                    or raw_item.get("biz_name"),
                    "biz_name": raw_item.get("biz_name")
                    or raw_item.get("display_name"),
                    "description": raw_item.get("description") or "",
                    "matched_phrases": raw_item.get("matched_phrases")
                    or (
                        [raw_item["matched_phrase"]]
                        if raw_item.get("matched_phrase")
                        else []
                    ),
                    "score": raw_item.get("score"),
                }
                try:
                    candidate = SemanticParseCandidate.model_validate(candidate_payload)
                except ValidationError as exc:
                    raise QuestionUnderstandingError(
                        "SEMANTIC_PARSE_CANDIDATE_INVALID",
                        details={
                            "group": group_name,
                            "errors": exc.errors(include_url=False),
                        },
                    ) from exc
                expected_type = {
                    "metrics": "METRIC",
                    "dimensions": "DIMENSION",
                }[group_name]
                if candidate.asset_type != expected_type:
                    raise QuestionUnderstandingError(
                        "SEMANTIC_PARSE_CANDIDATE_TYPE_MISMATCH",
                        details={
                            "group": group_name,
                            "asset_type": candidate.asset_type,
                        },
                    )
                groups[group_name].append(candidate)
        return SemanticParseCandidateContext(
            rewrite_question=question,
            candidate_groups=groups,
        )

    @staticmethod
    def _validate_candidate_refs(
        output: SemanticParseOutput,
        context: SemanticParseCandidateContext,
    ) -> None:
        candidates = {
            item.ref: item.asset_type
            for items in context.candidate_groups.values()
            for item in items
        }
        metric_refs = {item.ref for item in context.candidate_groups.get("metrics", [])}
        dimension_refs = {
            item.ref for item in context.candidate_groups.get("dimensions", [])
        }

        def require_refs(refs: list[str], allowed: set[str], field_name: str) -> None:
            unknown = sorted(set(refs) - allowed)
            if unknown:
                raise QuestionUnderstandingError(
                    "SEMANTIC_PARSE_ASSET_REF_OUT_OF_CANDIDATES",
                    details={"field": field_name, "refs": unknown},
                )

        require_refs(
            [item.ref for item in output.measures],
            metric_refs,
            "measures",
        )
        require_refs(
            list(output.dimension_group_refs()),
            dimension_refs,
            "operations.group.target_ref",
        )
        require_refs(
            [item.target_ref for item in output.filters],
            set(candidates),
            "filters",
        )
        require_refs(
            [
                item.target_ref
                for item in output.operations
                if item.type == "sort" and item.target_ref is not None
            ],
            set(candidates),
            "operations.sort.target_ref",
        )
        require_refs(
            [ref for item in output.unresolved for ref in item.candidate_refs],
            set(candidates),
            "unresolved.candidate_refs",
        )
        if output.multi_step is not None and output.multi_step.type == "fixed_drilldown":
            require_refs(
                list(output.multi_step.metric_refs),
                metric_refs,
                "multi_step.metric_refs",
            )
            require_refs(
                [
                    ref
                    for level in output.multi_step.levels
                    for ref in level.dimension_refs
                ],
                dimension_refs,
                "multi_step.levels.dimension_refs",
            )
        elif (
            output.multi_step is not None
            and output.multi_step.type == "fixed_attribution"
        ):
            require_refs(
                [output.multi_step.metric_ref],
                metric_refs,
                "multi_step.metric_ref",
            )
            require_refs(
                [output.multi_step.dimension_ref],
                dimension_refs,
                "multi_step.dimension_ref",
            )
        elif (
            output.multi_step is not None
            and output.multi_step.type == "limited_multistep"
        ):
            require_refs(
                list(output.multi_step.metric_refs),
                metric_refs,
                "multi_step.metric_refs",
            )
            require_refs(
                list(output.multi_step.dimension_refs),
                dimension_refs,
                "multi_step.dimension_refs",
            )
        elif (
            output.multi_step is not None
            and output.multi_step.type == "dynamic_research"
        ):
            require_refs(
                list(output.multi_step.required_dimension_refs),
                dimension_refs,
                "multi_step.required_dimension_refs",
            )
            require_refs(
                list(output.multi_step.required_driver_metric_refs),
                metric_refs,
                "multi_step.required_driver_metric_refs",
            )
        duplicated_measure_refs = _duplicated_refs(
            [item.ref for item in output.measures]
        )
        if duplicated_measure_refs:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_DUPLICATED_MEASURES",
                details={"refs": duplicated_measure_refs},
            )
        duplicated_group_refs = _duplicated_refs(list(output.dimension_group_refs()))
        if duplicated_group_refs:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_DUPLICATED_GROUP_BY",
                details={"refs": duplicated_group_refs},
            )
        time_groupings = [
            item.time_grain
            for item in output.operations
            if item.type == "group" and item.time_grain is not None
        ]
        if len(time_groupings) > 1:
            raise QuestionUnderstandingError("SEMANTIC_PARSE_DUPLICATED_TIME_GROUP")
        limit_operations = [item for item in output.operations if item.type == "limit"]
        if len(limit_operations) > 1:
            raise QuestionUnderstandingError("SEMANTIC_PARSE_DUPLICATED_LIMIT")
        for item in output.filters:
            target_type = candidates[item.target_ref]
            expected_type = "METRIC" if item.stage == "having" else "DIMENSION"
            if target_type != expected_type:
                raise QuestionUnderstandingError(
                    "SEMANTIC_PARSE_FILTER_TARGET_TYPE_INVALID",
                    details={
                        "target_ref": item.target_ref,
                        "stage": item.stage,
                        "asset_type": target_type,
                    },
                )

        if output.status == "resolved" and output.unresolved:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_RESOLVED_WITH_UNRESOLVED_FIELDS"
            )
        if output.status == "resolved" and not candidates:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_RESOLVED_WITHOUT_CANDIDATES"
            )


def _duplicated_refs(refs: list[str]) -> list[str]:
    """返回重复的候选引用，保持错误内容稳定。"""

    return sorted({ref for ref in refs if refs.count(ref) > 1})


SEMANTIC_PARSE_SYSTEM_PROMPT = """
你是 问数场景下的用户问题解析专家，你能够根据用户的问题和候选的语义资产进行问题解析，并给出对应的 JSON 。

你可以从候选资产中选择用户真正需要的指标和维度，并解析时间表达、维度值、筛选、排序、数量和计算要求。

严格规则：
1. measures 和 required_driver_metric_refs 只能引用 candidate_groups.metrics 中的 ref。
2. operations 中普通维度分组和 required_dimension_refs 只能引用 candidate_groups.dimensions 中的 ref。
3. filters 和排序操作只能引用候选资产中的 ref。
4. 不得创建候选列表之外的 ref，不得输出 asset_id、model_id 代替 ref。
5. 时间表达保留用户原话，不绑定时间字段；维度值保留用户原始值，不检索维度值资产。
6. 不生成 SQL、表名、字段名、查询计划或最终回答。
7. status=resolved 时 unresolved 必须为空数组；无法安全确定时使用 needs_clarification。
8. 所有没有内容的数组必须返回 []；用户没有明确要求的操作不得写入 operations。
9. 候选资产与用户名称唯一、明确匹配时必须直接选择对应 ref，不能仅因为任务进入
   dynamic_research 就把已经明确的指标或维度标记为 unresolved。

输出结构：
{
  "status": "resolved | needs_clarification | missed",
  "measures": [{"ref": "METRIC:..."}],
  "filters": [{"target_ref": "...", "operator": "=", "value": "...", "stage": "where | having"}],
  "time_filters": [{"expression": "用户原始时间表达", "role": "single | current | previous"}],
  "operations": [
    {"type": "group", "target_ref": "DIMENSION:...", "time_grain": null},
    {"type": "group", "target_ref": null, "time_grain": "day | week | month | quarter | year"},
    {"type": "sort", "target_ref": "METRIC:... | DIMENSION:...", "direction": "asc | desc"},
    {"type": "limit", "value": 5},
    {
      "type": "calculate",
      "calculation": "merge | growth_rate | difference | ratio | share | topn_other | pivot | expr | contribution",
      "current_time_role": "current | null",
      "previous_time_role": "previous | null",
      "details": {
        "metric_ref": "参与计算的指标候选 ref",
        "metric_refs": ["参与计算的指标候选 ref"],
        "numerator_ref": "占比计算的分子指标候选 ref",
        "denominator_ref": "占比计算的分母指标候选 ref",
        "result_name": "计算结果名称"
      }
    }
  ],
  "multi_step": null 或以下一种：
  {
    "type": "fixed_drilldown",
    "metric_refs": ["METRIC:..."],
    "levels": [
      {"id": "region", "dimension_refs": ["DIMENSION:..."]},
      {"id": "city", "dimension_refs": ["DIMENSION:...", "DIMENSION:..."]}
    ],
    "include_total": true,
    "primary_level": "city"
  }
  或
  {
    "type": "limited_multistep",
    "objective": "执行前可以完整确定的有限多步分析目标",
    "metric_refs": ["METRIC:..."],
    "dimension_refs": ["DIMENSION:..."],
    "allowed_time_roles": ["current", "previous"],
    "requested_outputs": ["用户明确要求的结果"]
  }
  或
  {
    "type": "fixed_attribution",
    "metric_ref": "METRIC:...",
    "dimension_ref": "DIMENSION:...",
    "current_time_role": "current",
    "previous_time_role": "previous",
    "method": "additive_change_contribution"
  }
  或
  {
    "type": "dynamic_research",
    "goal": "需要继续探索的目标",
    "reason": "result_driven_filter | result_driven_dimension | open_ended_cause | data_driven_stop_condition",
    "required_dimension_refs": ["用户明确要求必须分析或下钻的 DIMENSION ref"],
    "required_driver_metric_refs": ["用户明确要求必须验证的驱动 METRIC ref"],
    "premise_to_verify": {
      "premise_type": "metric_change | metric_anomaly | user_assertion",
      "metric_ref": "待验证前提对应的 METRIC ref",
      "expected_direction": "increase | decrease | stable | unknown",
      "time_roles": ["current | previous | single"],
      "statement": "用户陈述的待验证事实"
    }
  },
  "unresolved": [{"type": "...", "text": "...", "reason": "...", "candidate_refs": []}]
}
固定下钻只有在所有层级执行前都明确时使用；后一层 dimension_refs 必须包含前一层。
固定归因只有在指标、归因维度、当前期和对比期均明确时使用。
当任务需要组合多个查询和白名单计算、全部节点可以在执行前确定，但固定下钻、固定归因
以及 operations 无法完整表达拓扑时，输出 limited_multistep。
当一个计算结果还要作为另一个计算的输入时，必须输出 limited_multistep，不能只在
operations 中平铺多个互相没有输入关系的计算。
以下组合都属于有依赖的有限多步，必须输出 limited_multistep，并删除对应 calculate 操作：
- 先 difference 或 growth_rate，再 topn_other、share 或 pivot；
- 先 merge，再 ratio、share、topn_other 或 pivot；
- 先计算比率，再比较该比率。
例如“对比两个日期各档口GMV，先算差值，再取差值Top3并汇总其他”必须输出：
{
  "operations": [],
  "multi_step": {
    "type": "limited_multistep",
    "objective": "计算两个日期各档口GMV差值，再输出差值Top3和其他汇总",
    "metric_refs": ["对应的指标ref"],
    "dimension_refs": ["对应的档口维度ref"],
    "allowed_time_roles": ["current", "previous"],
    "requested_outputs": ["差值Top3和其他汇总"]
  }
}
如果下一节点的类型或筛选对象无法在执行前确定，必须输出 dynamic_research。
如果只是选择固定的 Top N、固定的下钻维度，且完整 DAG 可以在执行前声明，仍应
输出 fixed_drilldown、limited_multistep 或普通 calculate 操作。
判断目标变化主要由哪个驱动因素导致时，如果驱动指标集合或后续分析维度尚未明确，
属于验证方向依赖证据，必须输出 dynamic_research（reason=open_ended_cause）；如果用户
已经列出全部待验证驱动指标、分析维度和时间口径，则保留这些结构化绑定，交由计划
完整性判断是否可以固定执行。
开放式原因探索只有在归因维度、驱动指标或后续节点类型无法在执行前确定时才输出
dynamic_research（reason=open_ended_cause）。如果用户已经明确指定需要验证的驱动指标、
分析维度和时间口径，且完整 DAG 可以声明，应保留这些结构化字段供计划完整性判断，
不能仅因为出现“原因”“导致”等措辞进入 Research。
dynamic_research 只表示后续查询方向依赖中间结果。如果当前指标和维度已经由候选唯一
确定，status 仍然返回 resolved，measures 和普通维度 group 操作保留这些 ref，unresolved 返回 []。
用户明确指定的分析维度和驱动指标必须写入 dynamic_research.required_*；
例如“同时从商家和档口分析”保留两个 required_dimension_refs，“分别验证订单数和客单价”
保留两个 required_driver_metric_refs。required_* 只能引用候选资产，不能把允许探索的全部
Scope 写进去。用户明确要求 contribution 时，operations 中还必须同时包含一条
type=calculate、calculation=contribution，且贡献维度必须保留在普通维度 group 操作或 dynamic_research.required_dimension_refs；
服务端只把该结构化计算要求视为必须完成的贡献度目标。
用户陈述“某指标已经上升/下降/异常”等待验证事实时，必须填写 premise_to_verify；开放式探索
没有待验证事实时必须为 null，不能为了触发固定比较而虚构前提。
用户只说“深入分析”不能单独触发 dynamic_research。如果全部查询、计算和依赖在执行前
可以确定，仍应使用普通 calculate 操作、fixed_drilldown、fixed_attribution 或
limited_multistep。只有筛选值、分析对象、维度、验证方向或停止条件必须依赖中间结果时，
才能使用 dynamic_research。
""".strip()


def build_semantic_parse_user_prompt(
    context: SemanticParseCandidateContext,
) -> str:
    """构造只包含重写问题和候选资产的模型输入。"""

    payload = context.model_dump(mode="json")
    return (
        "请解析下面的问题，并严格按照系统定义返回 JSON。"
        "只能使用 candidate_groups 中存在的 ref：\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


__all__ = [
    "SEMANTIC_PARSE_SYSTEM_PROMPT",
    "SemanticParseCandidate",
    "SemanticParseCandidateContext",
    "SemanticParseService",
    "build_semantic_parse_user_prompt",
]
