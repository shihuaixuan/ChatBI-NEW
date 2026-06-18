from typing import Protocol

from apps.workflow_engine.domain.run import WorkflowRun


class RunStore(Protocol):
    """WorkflowRun 的持久化端口。"""

    def create(self, run: WorkflowRun) -> WorkflowRun: ...

    def get(self, run_id: str) -> WorkflowRun: ...

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun: ...
