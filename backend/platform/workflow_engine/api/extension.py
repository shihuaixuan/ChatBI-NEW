"""Workflow API 的业务装配端口。

通用引擎只声明 API 执行所需能力，具体图定义、运行时、会话校验和历史投影由
业务应用在组合根显式注册。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session

from sqlbot_platform.workflow_engine.api.chat_history import (
    GraphRecordProjectionGateway,
)
from sqlbot_platform.workflow_engine.domain.definition import WorkflowDefinition
from sqlbot_platform.workflow_engine.ports.run_store import RunStore
from sqlbot_platform.workflow_engine.runtime.graph_runtime import GraphRuntime


@dataclass(frozen=True, slots=True)
class ChatQueryPreparation:
    """业务侧完成会话校验并创建历史记录后的执行输入。"""

    dataset_id: int
    record_id: int
    conversation: dict[str, Any]


class WorkflowApiRequestError(ValueError):
    """业务装配端口返回的明确 HTTP 请求错误。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class WorkflowApiExtension(Protocol):
    """由业务应用实现的 Workflow API 扩展能力。"""

    def resolve_dataset_id(
        self,
        workspace_id: int,
        dataset_or_datasource_id: int,
    ) -> int: ...

    def validate_chat_query(
        self,
        *,
        workspace_id: int,
        user_id: int,
        chat_id: int,
        requested_dataset_id: int,
    ) -> int: ...

    def prepare_chat_query(
        self,
        *,
        workspace_id: int,
        user_id: int,
        chat_id: int,
        question: str,
        requested_dataset_id: int,
        run_id: str,
    ) -> ChatQueryPreparation: ...

    def build_runtime(
        self,
        definition_version: str,
        *,
        commit_events: bool,
        run_store: RunStore | None = None,
    ) -> GraphRuntime: ...

    def build_definition(self, definition_version: str) -> WorkflowDefinition: ...

    def build_record_projection_gateway(
        self,
    ) -> GraphRecordProjectionGateway: ...


WorkflowApiExtensionFactory = Callable[[Session], WorkflowApiExtension]

_extension_factory: WorkflowApiExtensionFactory | None = None


def register_workflow_api_extension(
    factory: WorkflowApiExtensionFactory,
) -> None:
    """在应用组合根注册唯一的业务扩展工厂。"""

    global _extension_factory
    _extension_factory = factory


def build_workflow_api_extension(session: Session) -> WorkflowApiExtension:
    """为当前数据库会话创建业务扩展；未注册时立即失败。"""

    if _extension_factory is None:
        raise RuntimeError("WORKFLOW_API_EXTENSION_NOT_REGISTERED")
    return _extension_factory(session)


__all__ = [
    "ChatQueryPreparation",
    "WorkflowApiExtension",
    "WorkflowApiExtensionFactory",
    "WorkflowApiRequestError",
    "build_workflow_api_extension",
    "register_workflow_api_extension",
]
