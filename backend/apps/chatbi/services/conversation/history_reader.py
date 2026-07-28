"""ChatBI 会话历史读取服务：富化会话详情并重新执行历史查询。

历史记录本体由 Conversation 的 HistoryQueryService 负责读取，本服务只补充
数据集/数据源展示信息（跨 semantic、datasource、assistant 领域），并在 ChatBI
侧完成 data_live 的重新执行。
"""

from typing import Any, cast

from apps.assistant.public import AssistantOutDsFactory
from apps.chatbi.services.generation import DYNAMIC_DATASOURCE_ASSISTANT_TYPES
from apps.conversation import ChatInfo, ConversationService, HistoryQueryService
from apps.datasource import (
    DatasourceQueryRequest,
    DatasourceQueryService,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.datasource.services import DatasourceNotFoundError
from apps.datasource.services.datasource_service import DatasourceService
from apps.semantic.services.dataset_catalog_service import (
    SemanticDatasetCatalogService,
)
from common.utils.data_format import DataFormat
from common.utils.utils import SQLBotLogUtil


class ConversationHistoryReader:
    """在 Conversation 历史结果之上补充数据集/数据源展示信息并执行 data_live。"""

    def __init__(
        self,
        conversation_service: ConversationService,
        history_service: HistoryQueryService,
        dataset_catalog_service: SemanticDatasetCatalogService,
        datasource_service: DatasourceService,
        query_service: DatasourceQueryService,
    ) -> None:
        self._conversation_service = conversation_service
        self._history_service = history_service
        self._dataset_catalog_service = dataset_catalog_service
        self._datasource_service = datasource_service
        self._query_service = query_service

    def get_chat_with_records(
        self,
        *,
        chart_id: int,
        current_user: Any,
        current_assistant: Any,
        with_data: bool = False,
        trans: Any = None,
    ) -> ChatInfo:
        workspace_id = current_user.oid if current_user.oid is not None else 1
        chat = self._conversation_service.get_owned_snapshot(
            user_id=current_user.id,
            workspace_id=workspace_id,
            chat_id=chart_id,
        )
        chat_info = ChatInfo(**chat.model_dump())

        dataset = (
            self._dataset_catalog_service.get_summary(chat.dataset_id)
            if chat.dataset_id
            else None
        )
        if not dataset:
            chat_info.dataset_exists = False
            chat_info.dataset_name = "Dataset not exist"
        else:
            chat_info.dataset_exists = True
            chat_info.dataset_name = dataset.name

        ds: Any
        if (
            current_assistant
            and current_assistant.type in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
            and chat.datasource is not None
        ):
            out_ds_instance = AssistantOutDsFactory.get_instance(current_assistant)
            ds = out_ds_instance.get_ds(chat.datasource, trans)
        else:
            ds = None
            if chat.datasource:
                try:
                    ds = self._datasource_service.get(chat.datasource)
                except DatasourceNotFoundError:
                    ds = None

        if not ds:
            chat_info.datasource_exists = False
            chat_info.datasource_name = "Datasource not exist"
        else:
            chat_info.datasource_exists = True
            chat_info.datasource_name = ds.name
            chat_info.ds_type = ds.type

        chat_info.records = cast(
            "list[Any]",
            self._history_service.list_records(
                user_id=current_user.id,
                chat_id=chart_id,
                with_data=with_data,
            ),
        )
        return chat_info

    def get_live_chart_data(
        self,
        *,
        current_user: Any,
        chat_record_id: int,
    ) -> dict[str, Any]:
        """按记录保存的数据源与 SQL 重新执行，返回统一结果结构。"""

        binding = self._history_service.get_live_query(
            user_id=current_user.id,
            chat_record_id=chat_record_id,
        )
        if binding is None:
            return {"status": "success", "data": [], "message": ""}
        workspace_id = current_user.oid if current_user.oid is not None else 1
        return self.execute_chart_data(
            binding.datasource_id,
            binding.sql,
            DatasourceQuerySubject(
                user_id=current_user.id,
                workspace_id=workspace_id,
            ),
        )

    def execute_chart_data(
        self,
        datasource_id: int | None,
        sql: str | None,
        subject: DatasourceQuerySubject,
    ) -> dict[str, Any]:
        json_result: dict[str, Any] = {
            "status": "success",
            "data": [],
            "message": "",
        }
        if datasource_id is None or sql is None:
            return json_result
        policy = self._query_service.resolve_policy(subject, datasource_id)
        result = self._query_service.execute(
            DatasourceQueryRequest(
                datasource_id=datasource_id,
                sql=sql,
                subject=subject,
                # 历史 SQL 没有保存选表快照，只能使用服务端授权表作为可信上限。
                selected_tables=policy.authorized_tables,
            )
        )
        if result.status != DatasourceQueryStatus.SUCCEEDED or result.data is None:
            SQLBotLogUtil.error(f"Function failed: {result.message}")
            json_result["status"] = "failed"
            json_result["message"] = result.message
            return json_result
        data = DataFormat.convert_large_numbers_in_object_array(  # type: ignore[no-untyped-call]
            result.data.full_data
        )
        json_result["data"] = (
            DataFormat.normalize_qualified_sql_column_keys_in_object_array(data)
        )
        return json_result


__all__ = ["ConversationHistoryReader"]
