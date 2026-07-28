"""ChatBI 专属澄清和 SQL 修正预算。"""

from __future__ import annotations

from dataclasses import dataclass

from apps.tool import BudgetVerdict, RetryAdvice, ToolResult, ToolStatus


@dataclass
class ChatBIBudgetPolicy:
    max_sql_retries: int = 2
    max_clarifications: int = 2
    sql_corrections: int = 0
    clarifications: int = 0
    pending_failed_sql: str | None = None

    def restore(self, snapshot: dict | None) -> None:
        if not snapshot:
            return
        self.sql_corrections = int(
            snapshot.get("sql_corrections")
            or snapshot.get("sql_failures")
            or 0
        )
        self.clarifications = int(snapshot.get("clarifications") or 0)
        pending = snapshot.get("pending_failed_sql")
        self.pending_failed_sql = str(pending) if pending else None

    def check_sql_call(self, sql: str) -> BudgetVerdict:
        normalized = self._normalize_sql(sql)
        if (
            self.pending_failed_sql is not None
            and normalized != self.pending_failed_sql
        ):
            self.sql_corrections += 1
            self.pending_failed_sql = None
            if self.sql_corrections > self.max_sql_retries:
                return BudgetVerdict(
                    False,
                    f"SQL 修正次数已达上限 {self.max_sql_retries} 次",
                    "sql_failed",
                )
        return BudgetVerdict(True)

    def record_sql_result(self, sql: str, result: ToolResult) -> None:
        if (
            result.status == ToolStatus.FAILED
            and result.retry_advice == RetryAdvice.CORRECT_INPUT
        ):
            self.pending_failed_sql = self._normalize_sql(sql)

    def record_clarification(self) -> BudgetVerdict:
        self.clarifications += 1
        if self.clarifications > self.max_clarifications:
            return BudgetVerdict(
                False,
                f"澄清次数已达上限 {self.max_clarifications} 次",
                "budget_exhausted",
            )
        return BudgetVerdict(True)

    def snapshot(self) -> dict:
        return {
            "sql_corrections": self.sql_corrections,
            "clarifications": self.clarifications,
            "pending_failed_sql": self.pending_failed_sql,
        }

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        return " ".join(sql.lower().split()).rstrip(";")


__all__ = ["ChatBIBudgetPolicy"]
