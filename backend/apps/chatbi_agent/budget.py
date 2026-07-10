"""预算与熔断：步数、token、重复调用、SQL 重试、墙钟超时。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import orjson


@dataclass
class BudgetVerdict:
    allowed: bool
    reason: str | None = None
    error_class: str | None = None


@dataclass
class BudgetGuard:
    max_steps: int = 12
    token_budget: int = 100_000
    repeat_fuse_threshold: int = 3
    max_sql_retries: int = 2
    timeout_seconds: int = 120

    steps: int = 0
    tokens_used: int = 0
    sql_failures: int = 0
    started_at: float = field(default_factory=time.monotonic)
    _last_call_key: str | None = None
    _repeat_count: int = 0

    def check_before_step(self) -> BudgetVerdict:
        if self.steps >= self.max_steps:
            return BudgetVerdict(False, f"已达最大步数 {self.max_steps}", "budget_exhausted")
        if self.token_budget and self.tokens_used >= self.token_budget:
            return BudgetVerdict(False, f"已达 token 预算 {self.token_budget}", "budget_exhausted")
        if time.monotonic() - self.started_at > self.timeout_seconds:
            return BudgetVerdict(False, f"已超时（>{self.timeout_seconds}s）", "budget_exhausted")
        return BudgetVerdict(True)

    def record_llm_turn(self, usage: dict | None) -> None:
        self.steps += 1
        if usage:
            self.tokens_used += int(usage.get("total_tokens") or 0)

    def check_tool_call(self, tool_name: str, args: dict) -> BudgetVerdict:
        key = tool_name + ":" + orjson.dumps(args, option=orjson.OPT_SORT_KEYS).decode()
        if key == self._last_call_key:
            self._repeat_count += 1
        else:
            self._last_call_key = key
            self._repeat_count = 1
        if self._repeat_count >= self.repeat_fuse_threshold:
            return BudgetVerdict(
                False,
                f"工具 {tool_name} 以相同参数连续调用 {self._repeat_count} 次，触发重复熔断",
                "budget_exhausted",
            )
        return BudgetVerdict(True)

    def record_sql_failure(self) -> BudgetVerdict:
        self.sql_failures += 1
        if self.sql_failures > self.max_sql_retries:
            return BudgetVerdict(
                False,
                f"SQL 执行失败重试已达上限 {self.max_sql_retries} 次",
                "sql_failed",
            )
        return BudgetVerdict(True)

    def snapshot(self) -> dict:
        return {
            "steps": self.steps,
            "max_steps": self.max_steps,
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
            "sql_failures": self.sql_failures,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 2),
        }
