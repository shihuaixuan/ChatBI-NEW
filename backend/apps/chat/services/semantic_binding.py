from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, col, select

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.datasource.models.datasource import CoreDatasource
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticModel,
)


class DatasetBindingError(ValueError):
    pass


DYNAMIC_DATASOURCE_ASSISTANT_TYPES = frozenset({1, 3})


class TenantContext(Protocol):
    oid: int | None


@dataclass(frozen=True)
class DatasetChatBinding:
    dataset_id: int
    dataset_name: str
    datasource_id: int
    datasource_name: str
    datasource_type: str
    datasource_type_name: str


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


def resolve_dataset_chat_binding(
    session: Session,
    current_user: TenantContext,
    dataset_id: int | None,
) -> DatasetChatBinding:
    if not dataset_id:
        raise DatasetBindingError("请选择数据集")

    oid = current_user.oid if current_user.oid is not None else 1
    dataset = session.get(SemanticDataset, dataset_id)
    if (
        not isinstance(dataset, SemanticDataset)
        or dataset.id is None
        or dataset.oid != oid
        or dataset.status != 1
    ):
        raise DatasetBindingError("数据集不存在或无权限访问")

    model = _resolve_dataset_model(session, oid, dataset)
    if model is None:
        raise DatasetBindingError("数据集未配置可用模型")

    datasource = session.get(CoreDatasource, model.datasource_id)
    if not isinstance(datasource, CoreDatasource) or datasource.id is None:
        raise DatasetBindingError("数据集未绑定可用数据源")
    if datasource.oid != oid:
        raise DatasetBindingError("数据集绑定的数据源无权限访问")

    return DatasetChatBinding(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        datasource_id=datasource.id,
        datasource_name=datasource.name,
        datasource_type=datasource.type,
        datasource_type_name=datasource.type_name,
    )


def _resolve_dataset_model(
    session: Session,
    oid: int,
    dataset: SemanticDataset,
) -> SemanticModel | None:
    if dataset.default_model_id:
        default_model = session.get(SemanticModel, dataset.default_model_id)
        if (
            isinstance(default_model, SemanticModel)
            and default_model.oid == oid
            and default_model.domain_id == dataset.domain_id
            and default_model.status == 1
        ):
            return default_model

    # 数据集没有默认模型时，按配置顺序选择第一个可用模型作为执行入口。
    statement = (
        select(SemanticModel)
        .join(
            SemanticDatasetModelConfig,
            col(SemanticDatasetModelConfig.model_id) == col(SemanticModel.id),
        )
        .where(
            SemanticDatasetModelConfig.oid == oid,
            SemanticDatasetModelConfig.dataset_id == dataset.id,
            SemanticDatasetModelConfig.status == 1,
            SemanticModel.oid == oid,
            SemanticModel.domain_id == dataset.domain_id,
            SemanticModel.status == 1,
        )
        .order_by(
            col(SemanticDatasetModelConfig.is_default).desc(),
            col(SemanticDatasetModelConfig.sort_order).asc(),
            col(SemanticModel.id).asc(),
        )
        .limit(1)
    )
    model = session.exec(statement).first()
    return model if isinstance(model, SemanticModel) else None


def apply_binding_to_chat(chat: Chat, binding: DatasetChatBinding) -> None:
    chat.dataset_id = binding.dataset_id
    chat.datasource = binding.datasource_id
    chat.engine_type = binding.datasource_type_name


def apply_binding_to_record(record: ChatRecord, binding: DatasetChatBinding) -> None:
    record.dataset_id = binding.dataset_id
    record.datasource = binding.datasource_id
    record.engine_type = binding.datasource_type_name
