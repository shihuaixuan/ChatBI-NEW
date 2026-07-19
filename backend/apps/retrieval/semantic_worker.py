"""Semantic 索引 worker 的兼容导入入口。"""

from apps.retrieval.worker import (
    process_index_jobs as process_semantic_index_jobs,
)
from apps.retrieval.worker import (
    submit_pending_index_jobs as submit_pending_semantic_index_jobs,
)

__all__ = ["process_semantic_index_jobs", "submit_pending_semantic_index_jobs"]
