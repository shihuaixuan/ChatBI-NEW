from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from apps.chatbi_workflow.capabilities.context import ChatBIRunContext
from apps.chatbi_workflow.schemas.v1 import AnswerOutput, FinalReplyOutput


@dataclass(frozen=True)
class AnswerGenerationPrompt:
    """回复生成模型提示词。"""

    system_prompt: str
    user_prompt: str


class AnswerModelClient(Protocol):
    """回复生成模型客户端协议。"""

    def __call__(self, prompt: AnswerGenerationPrompt) -> str: ...


def build_answer_generation_prompt(
    mode: str,
    question: str,
    projection: dict[str, Any],
) -> AnswerGenerationPrompt:
    """构造稳定 JSON 输出的回复提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的回复生成器，只生成用户可读回复，不要输出 Markdown 代码块，不要泄露内部变量、trace、SQL 原文或权限规则细节。

你必须只输出一个 JSON 对象，不能输出前后缀文本。JSON 字段如下：
{
  "answer": "给用户看的简洁中文回复",
  "warnings": [],
  "render_type": "text",
  "citations": []
}

回复要求：
- mode=reject 时，只说明当前问题无法处理或不在权限范围内，不提供绕过方法。
- mode=chitchat 时，简短回应，并引导用户提出业务数据分析问题。
- mode=generate 时，基于 projection 中已有结果生成回答；如果缺少结果，说明暂时无法生成完整回答。
- warnings 必须是字符串数组。
- render_type 默认使用 text。
- citations 必须是对象数组，没有引用时返回空数组。
""".strip()
    user_payload = {
        "mode": mode,
        "question": question,
        "projection": projection,
    }
    user_prompt = "请生成 ChatBI 回复，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return AnswerGenerationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_answer_projection(request: dict[str, Any]) -> dict[str, Any]:
    """构造答案模型最小输入，排除 SQL、候选 payload 和完整结果。"""

    ctx = ChatBIRunContext(request)
    execution = ctx.execution
    results = execution.get("results")
    if not isinstance(results, list):
        results = []
    if not results and execution:
        # 兼容 Step 3 之前的单查询扁平结果。
        results = [
            {
                "query_id": "query-0",
                "status": execution.get("status"),
                "row_count": execution.get("row_count", 0),
                "fields": execution.get("fields", []),
                "sample_rows": execution.get("rows", []),
                "execution_ms": execution.get("execution_ms", 0),
                "artifact_ref": execution.get("artifact_ref"),
                "error_code": execution.get("error_code"),
                "message": execution.get("message"),
            }
        ]
    projected_results = [
        _project_execution_result(item)
        for item in results
        if isinstance(item, dict)
    ]
    execution_projection = {
        "status": execution.get("status"),
        "validation": _project_validation(execution.get("validation")),
        "row_count": execution.get("row_count", 0),
        "results": projected_results,
        "error_code": execution.get("error_code"),
        "message": execution.get("message"),
    }
    analysis = _project_multi_query_analysis(ctx.plan, execution, results)
    if analysis:
        execution_projection["analysis"] = analysis
    decision = (
        ctx.knowledge.get("decision")
        if isinstance(ctx.knowledge.get("decision"), dict)
        else {}
    )
    return {
        "question": {
            "raw": ctx.raw_question,
            "rewritten": ctx.question,
        },
        "plan": _project_plan(ctx.plan),
        "execution": execution_projection,
        "knowledge_decision": {
            "status": decision.get("status"),
            "reason": decision.get("reason"),
        },
        "node_failure": _project_error(ctx.node_failure),
        "sql_error": _project_error(ctx.sql_error),
    }


def _project_plan(plan: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "status",
        "strategy",
        "select_mode",
        "metrics",
        "group_bys",
        "filters",
        "having",
        "time",
        "order",
        "limit",
        "issues",
        "infeasible_reason",
    )
    return {key: plan.get(key) for key in allowed if key in plan}


def _project_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "query_id",
        "status",
        "row_count",
        "fields",
        "sample_rows",
        "sampled_row_count",
        "result_truncated",
        "artifact_ref",
        "execution_ms",
        "error_code",
        "message",
    )
    return {key: result.get(key) for key in allowed if key in result}


def _project_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {}
    allowed = ("status", "issues", "suggestions")
    return {key: validation.get(key) for key in allowed if key in validation}


def _project_multi_query_analysis(
    plan: dict[str, Any],
    execution: dict[str, Any],
    results: list[Any],
) -> dict[str, Any]:
    if plan.get("strategy") != "multi_query":
        return {}
    role_results = _results_by_role(execution, results)
    if {"part", "total"}.issubset(role_results):
        return _share_analysis(role_results["part"], role_results["total"])
    if {"current", "baseline"}.issubset(role_results):
        return _comparison_analysis(role_results["current"], role_results["baseline"])
    return {}


def _results_by_role(execution: dict[str, Any], results: list[Any]) -> dict[str, dict[str, Any]]:
    queries = execution.get("queries")
    role_by_query_id = {}
    if isinstance(queries, list):
        role_by_query_id = {
            query.get("query_id"): query.get("role")
            for query in queries
            if isinstance(query, dict) and query.get("query_id") and query.get("role")
        }
    mapped: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        role = role_by_query_id.get(result.get("query_id"))
        if role:
            mapped[str(role)] = result
    return mapped


def _share_analysis(part_result: dict[str, Any], total_result: dict[str, Any]) -> dict[str, Any]:
    total_row = _first_row(total_result)
    total_metric, total = _first_numeric(total_row)
    if not total_metric or total in (None, 0):
        return {}
    rows = []
    for row in _sample_rows(part_result):
        value = _numeric_value(row.get(total_metric))
        metric = total_metric
        if value is None:
            metric, value = _first_numeric(row)
        if not metric or value is None:
            continue
        rows.append(
            {
                "dimensions": {key: val for key, val in row.items() if key != metric},
                "value": value,
                "share": value / total,
            }
        )
    if not rows:
        return {}
    return {"kind": "share", "metric": total_metric, "total": total, "rows": rows}


def _comparison_analysis(current_result: dict[str, Any], baseline_result: dict[str, Any]) -> dict[str, Any]:
    current_metric, current = _first_numeric(_first_row(current_result))
    baseline_metric, baseline = _first_numeric(_first_row(baseline_result))
    metric = current_metric or baseline_metric
    if not metric or current is None or baseline is None:
        return {}
    return {
        "kind": "comparison",
        "metric": metric,
        "current": current,
        "baseline": baseline,
        "delta": current - baseline,
        "change_rate": None if baseline == 0 else (current - baseline) / baseline,
    }


def _first_row(result: dict[str, Any]) -> dict[str, Any]:
    rows = _sample_rows(result)
    return rows[0] if rows else {}


def _sample_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("sample_rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _first_numeric(row: dict[str, Any]) -> tuple[str | None, float | None]:
    for key, value in row.items():
        number = _numeric_value(value)
        if number is not None:
            return key, number
    return None, None


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _project_error(error: dict[str, Any]) -> dict[str, Any]:
    allowed = ("node", "capability", "error_code", "message", "retryable")
    return {key: error.get(key) for key in allowed if key in error}


class DefaultAnswerModelClient:
    """默认回复模型客户端，复用项目已有 LLM 配置。"""

    def __init__(self) -> None:
        self._llm = None

    def __call__(self, prompt: AnswerGenerationPrompt) -> str:
        llm = self._get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=prompt.system_prompt),
                HumanMessage(content=prompt.user_prompt),
            ]
        )
        return str(getattr(response, "content", response) or "")

    def _get_llm(self):
        if self._llm is None:
            from apps.ai_model.model_factory import LLMFactory, get_default_config

            # 默认模型配置依赖异步解密逻辑；在已有事件循环中降级交给 adapter 处理。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError("answer model cannot be loaded inside a running event loop")
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


class AnswerAdapter:
    """ChatBI v1 回复节点真实能力适配器。"""

    def __init__(self, model_client: AnswerModelClient | None = None) -> None:
        self._model_client = model_client or DefaultAnswerModelClient()

    def reject(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成拒绝回复，模型不可用时返回稳定安全文案。"""

        return self._generate_with_model(
            "reject",
            request,
            fallback=self._answer_dump("当前问题无法在权限范围内回答。", ["answer_reject_fallback"]),
            parse_warning="answer_reject_parse_failed",
            model_warning="answer_reject_model_failed",
        )

    def chitchat(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成闲聊回复，模型不可用时返回固定引导文案。"""

        return self._generate_with_model(
            "chitchat",
            request,
            fallback=self._answer_dump("你好，我可以帮你分析业务数据问题。", ["answer_chitchat_fallback"]),
            parse_warning="answer_chitchat_parse_failed",
            model_warning="answer_chitchat_model_failed",
        )

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """生成业务回答，模型不可用时返回稳定降级文案。"""

        node_failure = ChatBIRunContext(request).node_failure
        fallback_text = "暂时无法生成完整回答，请稍后重试。"
        model_warning = "answer_generation_model_failed"
        if node_failure.get("error_code"):
            fallback_text = f"本次查询未能完成（{node_failure.get('error_code')}），请调整问题后重试。"
            model_warning = "answer_generation_degraded"
        return self._generate_with_model(
            "generate",
            request,
            fallback=self._answer_dump(fallback_text, [model_warning]),
            parse_warning="answer_generation_parse_failed",
            model_warning=model_warning,
        )

    def compose(self, request: dict[str, Any]) -> dict[str, Any]:
        """本地合成最终回复，保持前端响应契约稳定。"""

        ctx = ChatBIRunContext(request)
        final_answer = str(ctx.answer.get("answer") or "暂时无法生成完整回答，请稍后重试。")
        return FinalReplyOutput(
            final_answer=final_answer,
            recommendations=list(ctx.recommendations.get("questions") or []),
            chart=ctx.image_profile,
            metadata={"source": "real_chatbi_v1"},
        ).model_dump(mode="json")

    def _generate_with_model(
        self,
        mode: str,
        request: dict[str, Any],
        fallback: dict[str, Any],
        parse_warning: str,
        model_warning: str,
    ) -> dict[str, Any]:
        ctx = ChatBIRunContext(request)
        prompt = build_answer_generation_prompt(
            mode=mode,
            question=ctx.raw_question,
            projection=build_answer_projection(request),
        )
        try:
            model_text = self._model_client(prompt)
        except Exception:
            return self._fallback_with_warning(fallback, model_warning)
        try:
            payload = self._extract_json_object(model_text)
            output = AnswerOutput.model_validate(payload)
        except Exception:
            return self._fallback_with_warning(fallback, parse_warning)
        return output.model_dump(mode="json")

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        """从模型回复中提取第一个 JSON 对象。"""

        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("answer JSON object not found")

    @staticmethod
    def _answer_dump(answer: str, warnings: list[str]) -> dict[str, Any]:
        return AnswerOutput(
            answer=answer,
            warnings=warnings,
            render_type="text",
            citations=[],
        ).model_dump(mode="json")

    @staticmethod
    def _fallback_with_warning(fallback: dict[str, Any], warning: str) -> dict[str, Any]:
        result = dict(fallback)
        result["warnings"] = [warning]
        return result
