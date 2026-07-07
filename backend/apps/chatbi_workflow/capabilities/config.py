from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatBIConfig:
    """ChatBI v1 业务阈值与执行参数集中配置。"""

    candidate_accept_score: float = 0.85
    candidate_low_confidence_score: float = 0.65
    candidate_ambiguity_gap: float = 0.12
    sql_sample_row_limit: int = 50
    sql_max_parallel_queries: int = 4
    intent_subtask_timeout_seconds: float = 20.0
    intent_overall_timeout_seconds: float = 25.0
    intent_max_retry_count: int = 2
