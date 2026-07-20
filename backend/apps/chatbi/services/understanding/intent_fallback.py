"""自然语言意图的确定性降级推断服务。"""

from __future__ import annotations

import re
from typing import Any

from apps.chatbi.services.understanding.intent_projection import (
    time_range_from_mentions,
)


class QuestionIntentFallbackService:
    """模型不可用时使用轻量规则生成自然语言意图。"""

    @staticmethod
    def empty_intent() -> dict[str, Any]:
        """生成空问题的固定意图结果。"""

        return _intent_payload(
            "unknown",
            0.4,
            ["metric"],
            [],
        )

    def infer(self, question: str) -> dict[str, Any]:
        """按既有关键词优先级推断常见分析意图。"""

        if any(
            word in question
            for word in ("看一下情况", "分析一下", "怎么样", "看看数据")
        ):
            return _intent_payload(
                "unknown",
                0.4,
                ["metric"],
                [],
                required_slot_types=["metric"],
                query_shape={"select_mode": "unknown"},
            )

        metric_mentions = self._extract_metric_mentions(question)
        dimension_mentions = self._extract_dimension_mentions(question)
        time_mentions = self._extract_time_mentions(question)
        dimension_slots = self._dimension_slots_from_question(
            question,
            dimension_mentions,
        )
        time_range = time_range_from_mentions(
            time_mentions
        )

        if any(
            word in question
            for word in ("趋势", "走势", "变化", "按天", "按周", "按月")
        ):
            return _intent_payload(
                "trend_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "time_dimension"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "time_grain": self._infer_time_grain(question),
                },
            )
        if any(
            word in question
            for word in (
                "最高",
                "最低",
                "最好",
                "最差",
                "top",
                "Top",
                "前",
                "后",
                "排名",
            )
        ):
            return _intent_payload(
                "ranking_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "dimension", "order", "limit"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "needs_order_by": True,
                    "order_direction": self._infer_order_direction(question),
                    "limit": self._infer_limit(question),
                },
            )
        if any(word in question for word in ("同比", "环比", "对比", "较上期", "比较")):
            return _intent_payload(
                "comparison_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "comparison_target"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": bool(dimension_mentions),
                },
            )
        if any(word in question for word in ("占比", "构成", "比例")):
            return _intent_payload(
                "share_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "dimension"],
                query_shape={"select_mode": "share", "needs_group_by": True},
            )
        if any(
            word in question
            for word in (
                "异常",
                "波动",
                "下降原因",
                "上升原因",
                "为什么下降",
                "为什么上升",
            )
        ):
            return _intent_payload(
                "anomaly_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "time_range"],
                query_shape={"select_mode": "diagnostic"},
            )
        if any(word in question for word in ("明细", "详情", "列表", "清单")):
            return _intent_payload(
                "detail_query",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["dimension"],
                query_shape={"select_mode": "detail"},
            )
        return _intent_payload(
            "metric_query",
            0.85,
            [],
            [],
            metric_mentions=metric_mentions,
            dimension_mentions=dimension_mentions,
            time_mentions=time_mentions,
            dimension_slots=dimension_slots,
            time_range=time_range,
            required_slot_types=["metric"],
            query_shape={
                "select_mode": "aggregate",
                "needs_group_by": bool(dimension_mentions),
            },
        )

    def _dimension_slots_from_question(
        self,
        question: str,
        dimension_mentions: list[str],
    ) -> list[dict[str, Any]]:
        """抽取维度角色和值状态，值缺失时不伪造业务值。"""

        slots: list[dict[str, Any]] = []
        for dimension in dimension_mentions:
            value = self._extract_dimension_value(question, dimension)
            if value is not None:
                slots.append(
                    {
                        "name": dimension,
                        "role": "filter",
                        "value": value,
                        "value_status": "provided",
                    }
                )
                continue
            role = (
                "group_by"
                if self._is_group_by_dimension(question, dimension)
                else "ambiguous"
            )
            slots.append(
                {
                    "name": dimension,
                    "role": role,
                    "value": None,
                    "value_status": "not_provided",
                }
            )
        return slots

    @staticmethod
    def _extract_dimension_value(question: str, dimension: str) -> str | None:
        """识别“1号档口”或“档口1”这类常见维度值表达。"""

        patterns = (
            rf"([A-Za-z0-9一二三四五六七八九十百千万]+)\s*号?\s*{re.escape(dimension)}",
            rf"{re.escape(dimension)}\s*([A-Za-z0-9一二三四五六七八九十百千万]+)\s*号?",
        )
        for pattern in patterns:
            matched = re.search(pattern, question)
            if matched:
                return matched.group(1)
        return None

    @staticmethod
    def _is_group_by_dimension(question: str, dimension: str) -> bool:
        """判断用户是否明确要求按某个维度分组。"""

        group_markers = (
            f"各{dimension}",
            f"每个{dimension}",
            f"按{dimension}",
            f"分{dimension}",
            f"{dimension}排行",
            f"{dimension}排名",
        )
        ranking_markers = (
            "最高",
            "最低",
            "最好",
            "最差",
            "top",
            "Top",
            "前",
            "后",
            "排名",
        )
        return any(marker in question for marker in group_markers) or (
            dimension in question
            and any(marker in question for marker in ranking_markers)
        )

    @staticmethod
    def _extract_metric_mentions(question: str) -> list[str]:
        """提取常见自然语言指标线索，不确认语义资产。"""

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
        """提取常见自然语言维度线索，不确认语义资产。"""

        keywords = (
            "商品",
            "地区",
            "区域",
            "渠道",
            "店铺",
            "客户",
            "用户",
            "日期",
            "月份",
            "城市",
            "档口",
        )
        mentions = [keyword for keyword in keywords if keyword in question]
        if (
            any(word in question for word in ("按天", "按周", "按月"))
            and "日期" not in mentions
            and "月份" not in mentions
        ):
            mentions.append("日期")
        return mentions

    @staticmethod
    def _extract_time_mentions(question: str) -> list[str]:
        """提取自然语言时间线索。"""

        keywords = (
            "今天",
            "昨日",
            "昨天",
            "本周",
            "上周",
            "本月",
            "上月",
            "最近 7 天",
            "最近7天",
            "近 30 天",
            "近30天",
        )
        mentions = [keyword for keyword in keywords if keyword in question]
        absolute_months = re.findall(r"\d{4}\s*年\s*\d{1,2}\s*月", question)
        mentions.extend(month for month in absolute_months if month not in mentions)
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
        natural_limit = re.search(
            r"(?:最高|最低|最好|最差)(?:的)?\s*(\d+)\s*个",
            question,
        )
        if natural_limit:
            return int(natural_limit.group(1))
        for marker in ("Top", "top", "前", "后"):
            index = question.find(marker)
            if index < 0:
                continue
            digits = "".join(
                char
                for char in question[index + len(marker) : index + len(marker) + 3]
                if char.isdigit()
            )
            if digits:
                return int(digits)
        return None


def _intent_payload(
    intent_type: str,
    confidence: float,
    ambiguous_slots: list[str],
    conflict_slots: list[str],
    *,
    metric_mentions: list[str] | None = None,
    dimension_mentions: list[str] | None = None,
    time_mentions: list[str] | None = None,
    filter_mentions: list[dict[str, Any]] | None = None,
    dimension_slots: list[dict[str, Any]] | None = None,
    time_range: dict[str, Any] | None = None,
    required_slot_types: list[str] | None = None,
    query_shape: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造不包含执行器字段的稳定自然语言意图载荷。"""

    return {
        "intent_type": intent_type,
        "confidence": confidence,
        "metric_mentions": metric_mentions or [],
        "dimension_mentions": dimension_mentions or [],
        "dimension_slots": dimension_slots or [],
        "time_mentions": time_mentions or [],
        "time_range": time_range
        or {"raw": None, "value_status": "not_provided"},
        "filter_mentions": filter_mentions or [],
        "required_slot_types": required_slot_types or [],
        "query_shape": query_shape or {},
        "ambiguous_slots": ambiguous_slots,
        "conflict_slots": conflict_slots,
    }


__all__ = ["QuestionIntentFallbackService"]
