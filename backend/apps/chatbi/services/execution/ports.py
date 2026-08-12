"""执行子域端口（AGENTS.md v2 §5：可替换技术缝）。"""

from __future__ import annotations

from typing import Any, Protocol


class ResultArtifactGateway(Protocol):
    """ChatBI 依赖的通用 Artifact 存储与清理端口（防腐：引擎 Artifact → ChatBI 引用）。"""

    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Any: ...

    def get_json(self, artifact_id: str) -> Any: ...

    def schedule_cleanup(
        self,
        *,
        metadata: dict[str, str | int],
        execution_ids: list[str] | None = None,
    ) -> int: ...

    def process_pending_cleanup(self) -> int: ...


__all__ = ["ResultArtifactGateway"]
