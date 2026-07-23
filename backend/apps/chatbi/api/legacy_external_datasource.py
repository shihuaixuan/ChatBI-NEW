"""旧 Chat 流程的数据源运行时与外部助手 Schema 适配（台账 B3）。

本模块只服务旧 Chat 生成流程的本地/外部数据源装配；随 run_task 收口或
Assistant 外部数据源契约重构后删除，不是长期跨领域转换层。
"""

from dataclasses import dataclass
from typing import Any, cast

from sqlmodel import Session

from apps.assistant import AssistantOutDsSchema
from apps.assistant.public import AssistantOutDsFactory
from apps.chatbi.services.generation import DYNAMIC_DATASOURCE_ASSISTANT_TYPES
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
class LegacySchemaContext:
    """旧 LLMService 使用的 Schema 与样例数据。"""

    schema: str
    sample_data: str = ""


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

    datasource = build_datasource_service(session).get(datasource_id)
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
    "LegacySchemaContext",
    "build_legacy_external_datasource_catalog",
    "check_legacy_datasource_connection",
    "load_legacy_external_schema_context",
    "resolve_legacy_datasource",
]
