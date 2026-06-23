from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from apps.chatbi_workflow.schemas.v1 import (
    IntentRecognitionOutput,
    QuestionClassificationOutput,
    QuestionRewriteOutput,
)


@dataclass(frozen=True)
class QuestionClassificationPrompt:
    """问题分类模型提示词。"""

    system_prompt: str
    user_prompt: str


class QuestionClassificationModelClient(Protocol):
    """问题分类模型客户端协议，便于测试中替换真实大模型。"""

    def __call__(self, prompt: QuestionClassificationPrompt) -> str: ...


def build_question_classification_prompt(
    question: str,
    dataset_id: int | None,
    conversation_context: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造稳定 JSON 输出的分类提示词。"""

    context = conversation_context or {}
    system_prompt = """
你是 ChatBI 工作流中的问题分类器，只做问题分类，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "category": "forbidden | chitchat | data | followup",
  "reason": "不超过 40 个中文字符的分类原因",
  "risk_level": "low | medium | high",
  "confidence": 0.0
}

分类标准：
- forbidden：越权、绕过权限、危险操作、请求访问无授权数据、明显不应进入问数链路的问题。
- chitchat：问候、闲聊、能力咨询、非业务数据分析问题。
- data：完整的业务数据分析、统计、查询、趋势、排名、对比、归因问题。
- followup：依赖上文才能理解的追问，例如“那上个月呢”“按地区看一下”“继续分析利润”。

稳定性要求：
- category 只能取 forbidden、chitchat、data、followup。
- risk_level 只能取 low、medium、high。
- confidence 必须是 0 到 1 之间的数字。
- 不确定但像业务数据问题时，优先选择 data。
- 不确定但明显依赖上文时，优先选择 followup。
""".strip()
    user_payload = {
        "question": question,
        "dataset_id": dataset_id,
        "conversation_context": context,
    }
    user_prompt = "请分类以下 ChatBI 用户输入，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_question_rewrite_prompt(
    question: str,
    dataset_id: int | None = None,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造问题重写节点的稳定 JSON 提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的问题重写器，只做问题重写，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "rewritten_question": "补全上下文后的用户问题",
  "need_user_input": false,
  "missing_slots": [],
  "image_profile_hint": null
}

重写原则：
- 保留用户原始意图，不扩展不存在的业务条件。
- 如果问题依赖上文，需要结合 conversation_context 补全主语、时间、指标或维度。
- 如果可以判断适合的展示类型，可在 image_profile_hint 中给出 table、line、bar、pie 等简短提示；不确定时返回 null。

ChatBI 必需信息判定：
- dataset_id：语义数据集必须已经存在；如果 user_payload.dataset_id 不为空，说明上游已提供语义数据集，禁止把 dataset_id 放入 missing_slots；只有 user_payload.dataset_id 为空时才允许缺失槽位使用 dataset_id。
- metric 或 analysis_object：必须知道用户要分析什么指标、事实或业务对象，例如销售额、订单数、访问量、用户数、利润、客户、商品、订单。若问题只有“看一下情况”“分析一下”“怎么样”且上下文无法补全，need_user_input=true，missing_slots 包含 metric 或 analysis_object。
- time_range：默认不要因为缺少时间范围而澄清；很多业务问题可以先按系统默认时间或全量口径继续执行。只有用户明确要求趋势、对比、环比、同比、排行、按维度拆解，且缺失必要时间边界会导致问题不可执行或口径明显错误时，才把 time_range 放入 missing_slots。
- dimension：只有用户明确要求“按...看”“分...统计”“排行”“TopN”“对比不同...”但没有说明维度，且上下文无法补全时，才把 dimension 放入 missing_slots。
- filter：只有用户提到模糊对象或条件，例如“这个地区”“那个渠道”“这些客户”，且上下文无法解析时，才把 filter 放入 missing_slots。
- 不要为了追求完整而过度澄清；只要可以形成一个合理、可执行的数据问题，就应 need_user_input=false。
""".strip()
    user_payload = {
        "question": question,
        "dataset_id": dataset_id,
        "conversation_context": conversation_context or {},
        "user_feedback": user_feedback or {},
    }
    user_prompt = "请重写以下 ChatBI 用户输入，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_intent_recognition_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造意图识别节点的稳定 JSON 提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的意图识别器，只做意图识别，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "intent_type": "metric_query",
  "confidence": 0.0,
  "metric_mentions": [],
  "dimension_mentions": [],
  "time_mentions": [],
  "filter_mentions": [],
  "required_slot_types": [],
  "query_shape": {},
  "ambiguous_slots": [],
  "conflict_slots": []
}

可选 intent_type：
- metric_query：查询指标值或统计值，例如“今日访问量”“本月销售额”。
- trend_analysis：趋势、走势、按天、按周、按月变化。
- ranking_analysis：排行、最高、最低、TopN、前 N、后 N。
- comparison_analysis：同比、环比、对比、较上期、多个对象比较。
- detail_query：明细、详情、列表、清单。
- share_analysis：占比、构成、比例。
- anomaly_analysis：异常、波动、下降原因、为什么上升/下降。
- unknown：无法判断用户想做哪类分析。

检索线索要求：
- metric_mentions：用户原文或重写问题里疑似指标/事实/业务对象的自然语言短语，例如“销售额”“订单数”“访问人数”。不要输出 Headless asset_id、biz_name 或数据库字段名。
- dimension_mentions：用户原文或重写问题里疑似分组、排行、对比、明细展示维度的自然语言短语，例如“商品”“地区”“渠道”。不要输出 Headless asset_id。
- time_mentions：用户原文或重写问题里出现的时间范围、时间粒度或时间表达，例如“最近 7 天”“按月”“今天”。
- filter_mentions：用户原文或重写问题里出现的筛选条件，格式为 {"name": "自然语言条件名", "value": "自然语言条件值"}；没有明确条件时返回空数组。
- required_slot_types：后续生成可执行查询所需的槽位类型，只能使用 metric、dimension、time_dimension、time_range、filter、order、limit、comparison_target。
- query_shape：只描述查询形态，不引用任何真实资产 ID。可包含 needs_group_by、needs_order_by、order_direction、limit、time_grain、select_mode 等字段。
- 你不能选择真实指标、维度或枚举值 ID；资产确认由后续知识检索节点完成。

歧义和冲突判定：
- 如果不知道用户要分析的指标或业务对象，ambiguous_slots 包含 metric。
- 如果用户要求分组、排行或对比但没有给出维度，ambiguous_slots 包含 dimension。
- 如果用户使用“这个/那个/这些/上面”等指代且上下文无法解析，ambiguous_slots 包含 reference。
- 如果用户同时提出互相冲突的时间粒度或分析目标，conflict_slots 包含 time_grain 或 intent。
- confidence 必须是 0 到 1 之间的数字；低于 0.8 会触发意图澄清。
""".strip()
    user_payload = {
        "rewritten_question": rewritten_question,
        "conversation_context": conversation_context or {},
        "user_feedback": user_feedback or {},
    }
    user_prompt = "请识别以下 ChatBI 问题的分析意图，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


class DefaultQuestionClassificationModelClient:
    """默认问题分类模型客户端，复用项目已有 LLM 配置。"""

    def __init__(self) -> None:
        self._llm = None

    def __call__(self, prompt: QuestionClassificationPrompt) -> str:
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

            # 默认模型配置依赖异步解密逻辑；这里沿用现有工具层的同步调用方式。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError("question classification model cannot be loaded inside a running event loop")
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


class QuestionAdapter:
    """ChatBI v1 问题节点真实能力适配器。"""

    def __init__(self, model_client: QuestionClassificationModelClient | None = None) -> None:
        self._model_client = model_client or DefaultQuestionClassificationModelClient()

    def classify(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型完成问题分类，并把输出收敛为稳定 schema。"""

        raw_request = request.get("request", {})
        question = str(raw_request.get("question") or "").strip()
        dataset_id = raw_request.get("dataset_id")
        conversation_context = self._conversation_context(request)

        if not question:
            return self._dump("forbidden", "empty_question", "medium", 1.0)
        if dataset_id is None:
            return self._dump("forbidden", "missing_dataset", "medium", 1.0)

        prompt = build_question_classification_prompt(
            question=question,
            dataset_id=dataset_id,
            conversation_context=conversation_context,
        )
        try:
            model_text = self._model_client(prompt)
        except Exception as exc:
            raise RuntimeError("CLASSIFICATION_MODEL_CALL_FAILED") from exc

        try:
            payload = self._extract_json_object(model_text)
            output = QuestionClassificationOutput.model_validate(payload)
        except Exception as exc:
            raise ValueError("CLASSIFICATION_MODEL_OUTPUT_INVALID") from exc
        return output.model_dump(mode="json")

    def rewrite(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型补全上下文，输出稳定的问题重写结果。"""

        raw_request = request.get("request", {})
        question = str(raw_request.get("question") or "").strip()
        dataset_id = raw_request.get("dataset_id")
        variables = request.get("variables", {})
        user_feedback = variables.get("rewrite_response") if isinstance(variables, dict) else {}
        if not isinstance(user_feedback, dict):
            user_feedback = {}
        if not question:
            return self._rewrite_dump("", True, ["question"], None)

        prompt = build_question_rewrite_prompt(
            question=question,
            dataset_id=dataset_id,
            conversation_context=self._conversation_context(request),
            user_feedback=user_feedback,
        )
        try:
            model_text = self._model_client(prompt)
            payload = self._extract_json_object(model_text)
            output = QuestionRewriteOutput.model_validate(payload)
        except Exception:
            return self._rewrite_fallback(question, user_feedback)
        output = self._normalize_rewrite_output(output, dataset_id=dataset_id)
        return output.model_dump(mode="json")

    def recognize_intent(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型识别分析意图，并在失败时使用轻量规则兜底。"""

        raw_request = request.get("request", {})
        variables = request.get("variables", {})
        if not isinstance(variables, dict):
            variables = {}
        rewrite = variables.get("rewrite") if isinstance(variables.get("rewrite"), dict) else {}
        rewritten_question = str(rewrite.get("rewritten_question") or raw_request.get("question") or "").strip()
        user_feedback = variables.get("intent_response") if isinstance(variables.get("intent_response"), dict) else {}
        if not rewritten_question:
            return self._intent_dump("unknown", 0.4, ["metric"], [])

        prompt = build_intent_recognition_prompt(
            rewritten_question=rewritten_question,
            conversation_context=self._conversation_context(request),
            user_feedback=user_feedback,
        )
        try:
            model_text = self._model_client(prompt)
            payload = self._extract_json_object(model_text)
            output = IntentRecognitionOutput.model_validate(payload)
        except Exception:
            return self._intent_fallback(rewritten_question)
        return output.model_dump(mode="json")

    @staticmethod
    def _conversation_context(request: dict[str, Any]) -> dict[str, Any]:
        conversation = request.get("conversation")
        if isinstance(conversation, dict):
            return conversation
        return {}

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        """从模型回复中提取第一个 JSON 对象，兼容误输出的 Markdown 包裹。"""

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
        raise ValueError("classification JSON object not found")

    @staticmethod
    def _dump(category: str, reason: str, risk_level: str, confidence: float) -> dict[str, Any]:
        return QuestionClassificationOutput(
            category=category,
            reason=reason,
            risk_level=risk_level,
            confidence=confidence,
        ).model_dump(mode="json")

    @staticmethod
    def _rewrite_dump(
        rewritten_question: str,
        need_user_input: bool,
        missing_slots: list[str],
        image_profile_hint: str | None,
    ) -> dict[str, Any]:
        return QuestionRewriteOutput(
            rewritten_question=rewritten_question,
            need_user_input=need_user_input,
            missing_slots=missing_slots,
            image_profile_hint=image_profile_hint,
        ).model_dump(mode="json")

    @staticmethod
    def _normalize_rewrite_output(output: QuestionRewriteOutput, dataset_id: Any) -> QuestionRewriteOutput:
        """上游已提供 dataset_id 时，防御模型误把 dataset_id 当成缺失槽位。"""

        if dataset_id is None:
            return output
        missing_slots = [slot for slot in output.missing_slots if slot != "dataset_id"]
        if missing_slots == output.missing_slots:
            return output
        return output.model_copy(
            update={
                "missing_slots": missing_slots,
                "need_user_input": bool(missing_slots),
            }
        )

    @classmethod
    def _rewrite_fallback(cls, question: str, user_feedback: dict[str, Any]) -> dict[str, Any]:
        """模型不可用时保留最小澄清兜底，避免交互分支失去回归入口。"""

        need_user_input = not user_feedback and any(keyword in question for keyword in ("需要澄清", "信息不足", "补充"))
        return cls._rewrite_dump(
            question,
            need_user_input,
            ["metric"] if need_user_input else [],
            None,
        )

    @staticmethod
    def _intent_dump(
        intent_type: str,
        confidence: float,
        ambiguous_slots: list[str],
        conflict_slots: list[str],
        metric_mentions: list[str] | None = None,
        dimension_mentions: list[str] | None = None,
        time_mentions: list[str] | None = None,
        filter_mentions: list[dict[str, Any]] | None = None,
        required_slot_types: list[str] | None = None,
        query_shape: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return IntentRecognitionOutput(
            intent_type=intent_type,
            confidence=confidence,
            metric_mentions=metric_mentions or [],
            dimension_mentions=dimension_mentions or [],
            time_mentions=time_mentions or [],
            filter_mentions=filter_mentions or [],
            required_slot_types=required_slot_types or [],
            query_shape=query_shape or {},
            ambiguous_slots=ambiguous_slots,
            conflict_slots=conflict_slots,
        ).model_dump(mode="json")

    @classmethod
    def _intent_fallback(cls, question: str) -> dict[str, Any]:
        """模型不可用时用轻量规则识别常见分析意图。"""

        if any(word in question for word in ("看一下情况", "分析一下", "怎么样", "看看数据")):
            return cls._intent_dump(
                "unknown",
                0.4,
                ["metric"],
                [],
                required_slot_types=["metric"],
                query_shape={"select_mode": "unknown"},
            )
        metric_mentions = cls._extract_metric_mentions(question)
        dimension_mentions = cls._extract_dimension_mentions(question)
        time_mentions = cls._extract_time_mentions(question)
        if any(word in question for word in ("趋势", "走势", "变化", "按天", "按周", "按月")):
            return cls._intent_dump(
                "trend_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["metric", "time_dimension"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "time_grain": cls._infer_time_grain(question),
                },
            )
        if any(word in question for word in ("最高", "最低", "最好", "最差", "top", "Top", "前", "后", "排名")):
            return cls._intent_dump(
                "ranking_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["metric", "dimension", "order", "limit"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "needs_order_by": True,
                    "order_direction": cls._infer_order_direction(question),
                    "limit": cls._infer_limit(question),
                },
            )
        if any(word in question for word in ("同比", "环比", "对比", "较上期", "比较")):
            return cls._intent_dump(
                "comparison_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["metric", "comparison_target"],
                query_shape={"select_mode": "aggregate", "needs_group_by": bool(dimension_mentions)},
            )
        if any(word in question for word in ("占比", "构成", "比例")):
            return cls._intent_dump(
                "share_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["metric", "dimension"],
                query_shape={"select_mode": "share", "needs_group_by": True},
            )
        if any(word in question for word in ("异常", "波动", "下降原因", "上升原因", "为什么下降", "为什么上升")):
            return cls._intent_dump(
                "anomaly_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["metric", "time_range"],
                query_shape={"select_mode": "diagnostic"},
            )
        if any(word in question for word in ("明细", "详情", "列表", "清单")):
            return cls._intent_dump(
                "detail_query",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                required_slot_types=["dimension"],
                query_shape={"select_mode": "detail"},
            )
        return cls._intent_dump(
            "metric_query",
            0.85,
            [],
            [],
            metric_mentions=metric_mentions,
            dimension_mentions=dimension_mentions,
            time_mentions=time_mentions,
            required_slot_types=["metric"],
            query_shape={"select_mode": "aggregate", "needs_group_by": bool(dimension_mentions)},
        )

    @staticmethod
    def _extract_metric_mentions(question: str) -> list[str]:
        """从问题中提取常见自然语言指标线索，不做资产确认。"""

        keywords = (
            "销售额",
            "订单数",
            "访问人数",
            "访问量",
            "用户数",
            "利润",
            "GMV",
            "成交额",
            "收入",
            "客单价",
        )
        return [keyword for keyword in keywords if keyword in question]

    @staticmethod
    def _extract_dimension_mentions(question: str) -> list[str]:
        """从问题中提取常见自然语言维度线索，不做资产确认。"""

        keywords = ("商品", "地区", "区域", "渠道", "店铺", "客户", "用户", "日期", "月份", "城市", "档口")
        mentions = [keyword for keyword in keywords if keyword in question]
        if any(word in question for word in ("按天", "按周", "按月")) and "日期" not in mentions and "月份" not in mentions:
            mentions.append("日期")
        return mentions

    @staticmethod
    def _extract_time_mentions(question: str) -> list[str]:
        """从问题中提取自然语言时间线索。"""

        keywords = ("今天", "昨日", "昨天", "本周", "上周", "本月", "上月", "最近 7 天", "最近7天", "近 30 天", "近30天")
        mentions = [keyword for keyword in keywords if keyword in question]
        for keyword in ("按天", "按周", "按月"):
            if keyword in question:
                mentions.append(keyword)
        return mentions

    @staticmethod
    def _infer_time_grain(question: str) -> str | None:
        if "按月" in question:
            return "month"
        if "按周" in question:
            return "week"
        if "按天" in question or "趋势" in question or "走势" in question:
            return "day"
        return None

    @staticmethod
    def _infer_order_direction(question: str) -> str:
        if any(word in question for word in ("最低", "最差", "后")):
            return "asc"
        return "desc"

    @staticmethod
    def _infer_limit(question: str) -> int | None:
        for marker in ("Top", "top", "前", "后"):
            index = question.find(marker)
            if index < 0:
                continue
            digits = "".join(char for char in question[index + len(marker) : index + len(marker) + 3] if char.isdigit())
            if digits:
                return int(digits)
        return None
