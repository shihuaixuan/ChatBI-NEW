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
        result = self._model_service.invoke(
            QuestionModelInvocationData(
                stage="semantic_parse",
                system_prompt=SEMANTIC_PARSE_SYSTEM_PROMPT,
                user_prompt=build_semantic_parse_user_prompt(context),
                json_mode=QuestionModelJSONMode.STRICT,
            )
        )
        try:
            output = SemanticParseOutput.model_validate(result.payload)
        except ValidationError as exc:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_OUTPUT_INVALID",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        self._validate_candidate_refs(output, context)
        return output

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
                        details={"group": group_name, "errors": exc.errors(include_url=False)},
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
        metric_refs = {
            item.ref
            for item in context.candidate_groups.get("metrics", [])
        }
        dimension_refs = {
            item.ref
            for item in context.candidate_groups.get("dimensions", [])
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
            [item.ref for item in output.group_by],
            dimension_refs,
            "group_by",
        )
        require_refs(
            [item.target_ref for item in output.filters],
            set(candidates),
            "filters",
        )
        require_refs(
            [item.target_ref for item in output.order_by],
            set(candidates),
            "order_by",
        )
        require_refs(
            [ref for item in output.unresolved for ref in item.candidate_refs],
            set(candidates),
            "unresolved.candidate_refs",
        )
        duplicated_measure_refs = _duplicated_refs(
            [item.ref for item in output.measures]
        )
        if duplicated_measure_refs:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_DUPLICATED_MEASURES",
                details={"refs": duplicated_measure_refs},
            )
        duplicated_group_refs = _duplicated_refs(
            [item.ref for item in output.group_by]
        )
        if duplicated_group_refs:
            raise QuestionUnderstandingError(
                "SEMANTIC_PARSE_DUPLICATED_GROUP_BY",
                details={"refs": duplicated_group_refs},
            )
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
            raise QuestionUnderstandingError("SEMANTIC_PARSE_RESOLVED_WITHOUT_CANDIDATES")


def _duplicated_refs(refs: list[str]) -> list[str]:
    """返回重复的候选引用，保持错误内容稳定。"""

    return sorted({ref for ref in refs if refs.count(ref) > 1})


SEMANTIC_PARSE_SYSTEM_PROMPT = """
你是 问数场景下的用户问题解析专家，你能够根据用户的问题和候选的语义资产进行问题解析，并给出对应的 JSON 。

你可以从候选资产中选择用户真正需要的指标和维度，并解析时间表达、维度值、筛选、排序、数量和计算要求。

严格规则：
1. measures 只能引用 candidate_groups.metrics 中的 ref。
2. group_by 只能引用 candidate_groups.dimensions 中的 ref。
3. filters 和 order_by 只能引用候选资产中的 ref。
4. 不得创建候选列表之外的 ref，不得输出 asset_id、model_id 代替 ref。
5. 时间表达保留用户原话，不绑定时间字段；维度值保留用户原始值，不检索维度值资产。
6. 不生成 SQL、表名、字段名、查询计划或最终回答。
7. status=resolved 时 unresolved 必须为空数组；无法安全确定时使用 needs_clarification。
8. 所有没有内容的数组必须返回 []，没有明确数量时 limit 返回 null。

输出结构：
{
  "status": "resolved | needs_clarification | missed",
  "measures": [{"ref": "METRIC:..."}],
  "group_by": [{"ref": "DIMENSION:..."}],
  "filters": [{"target_ref": "...", "operator": "=", "value": "...", "stage": "where | having"}],
  "time_filters": [{"expression": "用户原始时间表达", "role": "single | current | previous"}],
  "order_by": [{"target_ref": "...", "direction": "asc | desc"}],
  "limit": null,
  "calculations": [{
    "type": "growth_rate | difference | ratio | share | ...",
    "current_time_role": "current | null",
    "previous_time_role": "previous | null",
    "details": {
      "metric_ref": "参与计算的指标候选 ref",
      "metric_refs": ["参与计算的指标候选 ref"],
      "numerator_ref": "占比计算的分子指标候选 ref",
      "denominator_ref": "占比计算的分母指标候选 ref",
      "result_name": "计算结果名称"
    }
  }],
  "unresolved": [{"type": "...", "text": "...", "reason": "...", "candidate_refs": []}]
}
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
