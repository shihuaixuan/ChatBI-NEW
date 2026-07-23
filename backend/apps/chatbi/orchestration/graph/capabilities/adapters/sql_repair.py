from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SQLRepairDecision:
    """SQL 修复策略输出，供工作流节点转成 v1 schema。"""

    action: str
    reason: str
    retryable: bool
    repair_hint: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def plan(self) -> dict[str, Any]:
        payload = {
            "action": self.action,
            "reason": self.reason,
            "retryable": self.retryable,
        }
        payload.update(self.metadata)
        return payload


class SQLRepairStrategy:
    """保守 SQL 修复策略，只产出计划，不直接重写或重试执行。"""

    name = "sql_repair"
    enabled = True

    def decide(
        self,
        *,
        error_code: str,
        message: str,
        sql: str | None = None,
        knowledge: dict[str, Any] | None = None,
    ) -> SQLRepairDecision:
        normalized_code = error_code.lower()
        normalized_message = message.lower()
        if normalized_code == "datasource_not_found":
            return SQLRepairDecision(
                action="check_datasource",
                reason="数据源不可用，不能自动重试",
                retryable=False,
                repair_hint="请检查数据集绑定的数据源是否存在，或当前用户是否有访问权限。",
            )
        if self._looks_like_missing_table(normalized_code, normalized_message):
            return SQLRepairDecision(
                action="regenerate_sql",
                reason="SQL 引用了不存在的表",
                retryable=True,
                repair_hint="表不存在，请基于已命中的语义表重新生成 SQL。",
                metadata={"candidate_tables": self._candidate_tables(knowledge or {})},
            )
        if self._looks_like_missing_column(normalized_code, normalized_message):
            return SQLRepairDecision(
                action="regenerate_sql",
                reason="SQL 引用了不存在或无权限的字段",
                retryable=True,
                repair_hint="字段不可用，请基于已命中的指标和维度重新生成 SQL。",
                metadata={"candidate_fields": self._candidate_fields(knowledge or {})},
            )
        if normalized_code == "SQL_EXECUTE_CONTEXT_REQUIRED".lower():
            return SQLRepairDecision(
                action="inspect_sql_generation",
                reason="SQL 执行缺少必要上下文",
                retryable=False,
                repair_hint="请检查 SQL 生成节点是否输出了 sql 和 datasource_id。",
            )
        if normalized_code == "SQL_EXECUTE_TOOL_REQUIRED".lower():
            return SQLRepairDecision(
                action="inspect_runtime",
                reason="运行时缺少 SQL 执行工具",
                retryable=False,
                repair_hint="请检查运行时是否注入了 SQL 执行工具。",
            )
        return SQLRepairDecision(
            action="manual_inspect",
            reason="暂未识别为可自动修复错误",
            retryable=False,
            repair_hint="请检查 SQL 生成结果、语义资产绑定和数据源连接状态。",
            metadata={"sql_present": bool(sql)},
        )

    @staticmethod
    def _looks_like_missing_table(error_code: str, message: str) -> bool:
        table_keywords = ("table", "relation", "doesn't exist", "not found", "unknown table", "表不存在")
        return "table" in error_code or any(keyword in message for keyword in table_keywords)

    @staticmethod
    def _looks_like_missing_column(error_code: str, message: str) -> bool:
        column_keywords = ("column", "field", "unknown column", "字段不存在", "列不存在")
        return "column" in error_code or any(keyword in message for keyword in column_keywords)

    @staticmethod
    def _candidate_tables(knowledge: dict[str, Any]) -> list[str]:
        raw_values = knowledge.get("tables")
        values: list[Any] = raw_values if isinstance(raw_values, list) else []
        return [str(value) for value in values if str(value or "").strip()]

    @staticmethod
    def _candidate_fields(knowledge: dict[str, Any]) -> list[str]:
        raw_values = knowledge.get("fields")
        values: list[Any] = raw_values if isinstance(raw_values, list) else []
        return [str(value) for value in values if str(value or "").strip()]
