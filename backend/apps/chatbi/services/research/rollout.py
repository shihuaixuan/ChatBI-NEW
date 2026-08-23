"""阶段 7：切流配置与确定性采样（doc38 §11.3.6）。

切流只发生在路由入口：``resolve_rollout`` 根据配置的执行模式
（legacy/shadow）和数据集白名单、租户白名单、采样比例，决定本次请求
是否附带 shadow 双跑。判定完全确定性——同一 ``run_key`` 永远得到同一
结果，便于复盘和回退审计。

回退语义：把 ``CHATBI_RESEARCH_EXECUTION_MODE`` 改回 legacy 即立即停止
双跑；新 Harness 内部绝不调用旧 ResearchPipeline 兜底，shadow 机制任何
环节失败都只会让该请求按纯 legacy 执行并记录原因。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

# 采样桶数量：sha256 映射到 [0, _BUCKETS) 后与比例比较，保证确定性。
_BUCKETS = 10_000

RolloutMode = Literal["legacy", "shadow"]

REASON_CONFIGURED_LEGACY = "configured_legacy"
REASON_SAMPLED_IN = "sampled_in"
REASON_SAMPLED_OUT = "sampled_out"
REASON_SAMPLE_RATE_ZERO = "sample_rate_zero"
REASON_DATASET_NOT_ALLOWED = "dataset_not_in_allowlist"
REASON_TENANT_NOT_ALLOWED = "tenant_not_in_allowlist"


@dataclass(frozen=True)
class RolloutDecision:
    effective_mode: RolloutMode
    reason: str


@dataclass(frozen=True)
class RolloutPolicy:
    """路由入口的切流策略；由 AgentConfig 字段构造。"""

    mode: str
    sample_rate: float = 0.0
    dataset_allowlist: tuple[int, ...] = ()
    tenant_allowlist: tuple[int, ...] = ()

    @classmethod
    def from_config(
        cls,
        *,
        mode: str,
        sample_rate: float = 0.0,
        dataset_allowlist: tuple[int, ...] = (),
        tenant_allowlist: tuple[int, ...] = (),
    ) -> RolloutPolicy:
        return cls(
            mode=mode,
            sample_rate=min(max(float(sample_rate), 0.0), 1.0),
            dataset_allowlist=tuple(dict.fromkeys(int(item) for item in dataset_allowlist)),
            tenant_allowlist=tuple(dict.fromkeys(int(item) for item in tenant_allowlist)),
        )

    def resolve(
        self,
        *,
        datasource_id: int | None,
        oid: int,
        run_key: str,
    ) -> RolloutDecision:
        """决定本次请求是否附带 shadow 双跑；legacy 模式直接放行。"""

        if self.mode != "shadow":
            return RolloutDecision("legacy", REASON_CONFIGURED_LEGACY)
        if self.dataset_allowlist and (
            datasource_id is None or datasource_id not in self.dataset_allowlist
        ):
            return RolloutDecision("legacy", REASON_DATASET_NOT_ALLOWED)
        if self.tenant_allowlist and oid not in self.tenant_allowlist:
            return RolloutDecision("legacy", REASON_TENANT_NOT_ALLOWED)
        bucket = int.from_bytes(
            hashlib.sha256(run_key.encode("utf-8")).digest()[:4], "big"
        ) % _BUCKETS
        threshold = round(self.sample_rate * _BUCKETS)
        if threshold <= 0:
            return RolloutDecision("legacy", REASON_SAMPLE_RATE_ZERO)
        if bucket >= threshold:
            return RolloutDecision("legacy", REASON_SAMPLED_OUT)
        return RolloutDecision("shadow", REASON_SAMPLED_IN)


__all__ = [
    "REASON_CONFIGURED_LEGACY",
    "REASON_DATASET_NOT_ALLOWED",
    "REASON_SAMPLED_IN",
    "REASON_SAMPLED_OUT",
    "REASON_SAMPLE_RATE_ZERO",
    "REASON_TENANT_NOT_ALLOWED",
    "RolloutDecision",
    "RolloutPolicy",
]
