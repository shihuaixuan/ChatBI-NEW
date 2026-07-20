"""执行请求与会话绑定一致性规则（纯函数）。"""

from apps.chatbi.errors import ExecutionBindingError
from apps.chatbi.models import ExecutionBinding, ExecutionBindingData


def resolve_execution_binding(data: ExecutionBindingData) -> ExecutionBinding:
    """统一校验 Agent 与 Graph 的数据集和数据源执行边界。"""

    dataset_id = data.conversation_dataset_id
    if data.requested_dataset_id is not None:
        if dataset_id is None or data.requested_dataset_id != dataset_id:
            raise ExecutionBindingError("CHAT_DATASET_MISMATCH")
    if data.require_dataset and dataset_id is None:
        raise ExecutionBindingError("CHAT_DATASET_REQUIRED")

    datasource_id = data.conversation_datasource_id
    if data.requested_datasource_id is not None:
        if (
            datasource_id is not None
            and data.requested_datasource_id != datasource_id
        ):
            raise ExecutionBindingError("CHAT_DATASOURCE_MISMATCH")
        datasource_id = data.requested_datasource_id
    if data.require_datasource and datasource_id is None:
        raise ExecutionBindingError("CHAT_DATASOURCE_REQUIRED")

    return ExecutionBinding(
        dataset_id=dataset_id,
        datasource_id=datasource_id,
    )


__all__ = ["ExecutionBindingError", "resolve_execution_binding"]
