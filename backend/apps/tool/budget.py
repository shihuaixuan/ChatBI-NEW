"""通用预算与熔断原语。

宿主（如 ChatBI AgentLoop）负责把 soft/hard 判定落实为 allowlist 与收口策略。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import orjson

PlanningMode = Literal["normal", "soft", "exhausted"]


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
    timeout_seconds: int = 120
    soft_ratio: float = 0.8

    steps: int = 0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.monotonic)
    _last_call_key: str | None = None
    _repeat_count: int = 0

    def restore(self, snapshot: dict | None) -> None:
        """从持久化快照恢复累计量（澄清恢复续跑时使用；墙钟重新计时）。"""

        if not snapshot:
            return
        self.steps = int(snapshot.get("steps") or 0)
        self.tokens_used = int(snapshot.get("tokens_used") or 0)

    def planning_mode(self) -> PlanningMode:
        """当前规划模式：normal / soft（接近上限）/ exhausted（已耗尽）。"""

        if self._is_exhausted():
            return "exhausted"
        if self._is_soft():
            return "soft"
        return "normal"

    def check_before_step(self) -> BudgetVerdict:
        if self.steps >= self.max_steps:
            return BudgetVerdict(False, f"已达最大步数 {self.max_steps}", "budget_exhausted")
        if self.token_budget and self.tokens_used >= self.token_budget:
            return BudgetVerdict(False, f"已达 token 预算 {self.token_budget}", "budget_exhausted")
        if time.monotonic() - self.started_at > self.timeout_seconds:
            return BudgetVerdict(False, f"已超时（>{self.timeout_seconds}s）", "budget_exhausted")
        return BudgetVerdict(True)

    def remaining_seconds(self) -> float:
        """返回本次 Run 调用剩余的墙钟时间。"""

        return max(self.timeout_seconds - (time.monotonic() - self.started_at), 0.0)

    def record_llm_turn(self, usage: dict | None) -> None:
        self.record_system_step()
        self.record_llm_usage(usage)

    def record_system_step(self) -> None:
        """记录不经过规划模型的确定性步骤，例如问题理解后的立即澄清。"""

        self.steps += 1

    def record_llm_usage(self, usage: dict | None) -> None:
        """累计不占规划步数的模型调用，例如前置问题理解。"""

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

    def snapshot(self) -> dict:
        return {
            "steps": self.steps,
            "max_steps": self.max_steps,
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
            "planning_mode": self.planning_mode(),
            "elapsed_seconds": round(time.monotonic() - self.started_at, 2),
        }

    def _is_exhausted(self) -> bool:
        if self.steps >= self.max_steps:
            return True
        if self.token_budget and self.tokens_used >= self.token_budget:
            return True
        if time.monotonic() - self.started_at > self.timeout_seconds:
            return True
        return False

    def _is_soft(self) -> bool:
        ratio = self.soft_ratio
        if ratio <= 0 or ratio >= 1:
            return False
        if self.max_steps > 0 and self.steps >= max(1, int(self.max_steps * ratio)):
            return True
        if (
            self.token_budget > 0
            and self.tokens_used >= max(1, int(self.token_budget * ratio))
        ):
            return True
        return False
