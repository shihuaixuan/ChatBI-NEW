"""ChatBI 会话创建与联合删除需要的技术端口。"""

from typing import Protocol


class ExecutionCleanupGateway(Protocol):
    """清理一种执行方式的运行数据并提交自身事务。"""

    def delete_for_chat(self, chat_id: int) -> int: ...


class ArtifactCleanupGateway(Protocol):
    """登记并执行 Workflow Artifact 正文清理。"""

    def schedule_chat_cleanup(
        self,
        chat_id: int,
        *,
        legacy_execution_ids: list[str],
    ) -> int: ...

    def process_pending_cleanup(self) -> int: ...


__all__ = [
    "ArtifactCleanupGateway",
    "ExecutionCleanupGateway",
]
