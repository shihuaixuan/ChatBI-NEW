from collections.abc import Callable
from time import sleep as system_sleep

from apps.workflow_engine.domain.definition import RetryPolicy


class RetryController:
    """计算有限指数退避，并允许测试注入无等待 Clock。"""

    def __init__(self, sleep: Callable[[float], None] = system_sleep) -> None:
        self._sleep = sleep

    def wait(self, policy: RetryPolicy, failed_attempt: int) -> None:
        delay_ms = policy.initial_delay_ms * (policy.backoff_multiplier ** (failed_attempt - 1))
        bounded_delay_ms = min(delay_ms, policy.max_delay_ms)
        if bounded_delay_ms > 0:
            self._sleep(bounded_delay_ms / 1000)
