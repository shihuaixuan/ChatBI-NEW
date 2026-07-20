"""旧 LLMService 尚未迁移完成的运行依赖。

本模块只服务旧 Chat 生成流程，后续应随 LLMService 收缩而删除。
"""

from dataclasses import dataclass
from typing import Any, cast

from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.ai_model.model_factory import LLMFactory, get_default_config
from apps.ai_model.models.dto import LLMConfig
from apps.assistant import AssistantOutDsSchema
from apps.assistant.public import AssistantOutDsFactory
from apps.chatbi.services import DYNAMIC_DATASOURCE_ASSISTANT_TYPES
from apps.datasource import (
    DatasourceConnection,
    DatasourceRecord,
    build_external_datasource_connection,
)
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_service,
)
from apps.datasource.database import check_connection, get_version


@dataclass(frozen=True, slots=True)
class LegacyDatasourceRuntime:
    """旧 LLMService 使用的数据源运行快照。"""

    datasource: DatasourceRecord | AssistantOutDsSchema
    connection: DatasourceConnection
    engine: str
    conversation_engine_type: str | None
    external_catalog: Any | None = None

    @property
    def is_external(self) -> bool:
        return self.external_catalog is not None


@dataclass(frozen=True, slots=True)
class LegacyModelRuntime:
    """旧 LLMService 使用的模型运行快照。"""

    config: LLMConfig
    llm: BaseChatModel


@dataclass(frozen=True, slots=True)
class LegacySchemaContext:
    """旧 LLMService 使用的 Schema 与样例数据。"""

    schema: str
    sample_data: str = ""


def get_legacy_local_datasource(
    session: Session,
    datasource_id: int,
) -> DatasourceRecord:
    """通过 Datasource 公开 Service 读取本地数据源。"""

    return build_datasource_service(session).get(datasource_id)


def resolve_legacy_datasource(
    session: Session,
    datasource_id: int,
    current_assistant: Any | None,
    external_catalog: Any | None = None,
) -> LegacyDatasourceRuntime:
    """解析旧 LLMService 使用的本地或外部数据源。"""

    if (
        current_assistant is not None
        and current_assistant.type in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
    ):
        catalog = external_catalog or build_legacy_external_datasource_catalog(
            current_assistant
        )
        if catalog is None:
            raise ValueError("External datasource catalog is required")
        datasource = catalog.get_ds(datasource_id)
        if datasource is None:
            raise ValueError("No available datasource configuration found")
        connection = build_external_datasource_connection(datasource, 10)
        return LegacyDatasourceRuntime(
            datasource=datasource,
            connection=connection,
            engine=connection.type + get_version(connection),
            conversation_engine_type=datasource.type,
            external_catalog=catalog,
        )

    datasource = get_legacy_local_datasource(session, datasource_id)
    connection = DatasourceConnection.model_validate(datasource)
    version = build_datasource_connection_service(session).get_version(datasource_id)
    engine_type = (
        datasource.type_name if datasource.type != "excel" else "PostgreSQL"
    )
    return LegacyDatasourceRuntime(
        datasource=datasource,
        connection=connection,
        engine=(engine_type or datasource.type) + version,
        conversation_engine_type=datasource.type_name,
    )


def build_legacy_external_datasource_catalog(
    current_assistant: Any | None,
) -> Any | None:
    """为旧流程装配外部 Assistant 数据源目录。"""

    if (
        current_assistant is None
        or current_assistant.type not in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
    ):
        return None
    return AssistantOutDsFactory.get_instance(current_assistant)


def check_legacy_datasource_connection(
    session: Session,
    runtime: LegacyDatasourceRuntime,
) -> bool:
    """检查旧流程当前数据源连接。"""

    datasource_id = runtime.datasource.id
    if datasource_id is None:
        return False
    if runtime.is_external:
        return bool(check_connection(ds=runtime.connection, trans=None))
    return build_datasource_connection_service(session).check_connection(
        int(datasource_id)
    )


async def build_legacy_model_runtime(
    specialized_model_id: str | int | None,
    *,
    no_reasoning: bool,
) -> LegacyModelRuntime:
    """装配旧流程使用的默认或助手指定模型。"""

    model_id = (
        int(specialized_model_id)
        if specialized_model_id is not None
        else None
    )
    config = await get_default_config(model_id)
    if no_reasoning and config.additional_params:
        extra_body = config.additional_params.get("extra_body")
        if isinstance(extra_body, dict):
            extra_body.pop("enable_thinking", None)

    llm_instance: Any = LLMFactory.create_llm(config)
    return LegacyModelRuntime(config=config, llm=llm_instance.llm)


def load_legacy_external_schema_context(
    external_catalog: Any,
    *,
    datasource_id: int,
    question: str,
    embedding: bool,
    table_names: list[str] | None,
) -> LegacySchemaContext:
    """读取旧外部助手数据源的 Schema 上下文。"""

    schema = external_catalog.get_db_schema(
        datasource_id,
        question,
        embedding=embedding,
        table_list=cast(list[str], table_names),
    )
    return LegacySchemaContext(schema=schema)


__all__ = [
    "LegacyDatasourceRuntime",
    "LegacyModelRuntime",
    "LegacySchemaContext",
    "build_legacy_external_datasource_catalog",
    "build_legacy_model_runtime",
    "check_legacy_datasource_connection",
    "get_legacy_local_datasource",
    "load_legacy_external_schema_context",
    "resolve_legacy_datasource",
]
