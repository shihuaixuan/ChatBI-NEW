from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionBindingData:
    """会话绑定和执行请求共同决定的运行上下文。"""

    conversation_dataset_id: int | None
    conversation_datasource_id: int | None
    requested_dataset_id: int | None = None
    requested_datasource_id: int | None = None
    require_dataset: bool = False
    require_datasource: bool = True


@dataclass(frozen=True, slots=True)
class ExecutionBinding:
    dataset_id: int | None
    datasource_id: int | None


__all__ = ["ExecutionBinding", "ExecutionBindingData"]
