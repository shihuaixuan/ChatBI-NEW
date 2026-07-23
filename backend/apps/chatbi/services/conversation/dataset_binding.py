"""会话与数据集绑定规则（原 apps/chat/services/semantic_binding.py，R3-c2 迁入）。

数据集/模型解析走 Semantic 公开绑定服务，数据源信息走 Datasource 公开服务；
本模块只保留会话侧校验、文案与 ORM 赋值规则。
"""

from __future__ import annotations

from apps.chatbi.errors import ConversationBindingError
from apps.chatbi.models import Chat, ChatRecord, ConversationBinding
from apps.chatbi.services.generation.context.scope import (
    DYNAMIC_DATASOURCE_ASSISTANT_TYPES,
)
from apps.datasource.services import DatasourceService
from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.services.dataset_binding_service import SemanticDatasetBindingService

# 旧名兼容（随 R3-d 收口清理）。
DatasetBindingError = ConversationBindingError
DatasetChatBinding = ConversationBinding


def validate_assistant_dataset_binding(
    dataset_id: int | None,
    assistant_type: int | None,
) -> None:
    """外部动态数据源没有本地模型关系，不能声明 Semantic 数据集绑定。"""

    if (
        dataset_id is not None
        and assistant_type in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
    ):
        raise DatasetBindingError("外部动态数据源助手不能绑定本地 Semantic 数据集")


def resolve_conversation_binding(
    binding_service: SemanticDatasetBindingService,
    datasource_service: DatasourceService,
    *,
    workspace_id: int,
    dataset_id: int | None,
) -> ConversationBinding:
    if not dataset_id:
        raise DatasetBindingError("请选择数据集")

    try:
        binding = binding_service.resolve_execution_binding(workspace_id, dataset_id)
    except SemanticNotFoundError as exc:
        if "DATASET_MODEL_NOT_CONFIGURED" in str(exc):
            raise DatasetBindingError("数据集未配置可用模型") from exc
        raise DatasetBindingError("数据集不存在或无权限访问") from exc

    try:
        datasource = datasource_service.get(binding.datasource_id)
    except ValueError as exc:
        raise DatasetBindingError("数据集未绑定可用数据源") from exc
    if datasource.id is None:
        raise DatasetBindingError("数据集未绑定可用数据源")
    if datasource.oid != workspace_id:
        raise DatasetBindingError("数据集绑定的数据源无权限访问")

    return ConversationBinding(
        dataset_id=binding.dataset_id,
        dataset_name=binding.dataset_name,
        datasource_id=datasource.id,
        datasource_name=datasource.name,
        datasource_type=datasource.type,
        # DatasourceRecord.type_name 为可空 DTO 字段；此前经 pydantic 校验实际不为 None。
        datasource_type_name=datasource.type_name or "",
    )


def apply_binding_to_chat(chat: Chat, binding: ConversationBinding) -> None:
    chat.dataset_id = binding.dataset_id
    chat.datasource = binding.datasource_id
    chat.engine_type = binding.datasource_type_name


def apply_binding_to_record(record: ChatRecord, binding: ConversationBinding) -> None:
    record.dataset_id = binding.dataset_id
    record.datasource = binding.datasource_id
    record.engine_type = binding.datasource_type_name


__all__ = [
    "DatasetBindingError",
    "DatasetChatBinding",
    "apply_binding_to_chat",
    "apply_binding_to_record",
    "resolve_conversation_binding",
    "validate_assistant_dataset_binding",
]
