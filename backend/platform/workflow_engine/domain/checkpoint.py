from datetime import datetime

from pydantic import BaseModel, Field, PositiveInt

from sqlbot_platform.workflow_engine.domain.context import WorkflowContext


class WorkflowCheckpoint(BaseModel):
    """节点边界处的可恢复状态。"""

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: PositiveInt
    node_name: str = Field(min_length=1)
    context: WorkflowContext
    definition_digest: str = Field(min_length=1)
    created_at: datetime
