import concurrent
import os
import traceback
import urllib.parse
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, cast

import orjson
import pandas as pd
import requests
import sqlparse
from langchain.chat_models.base import BaseChatModel
from langchain_community.utilities import SQLDatabase
from sqlalchemy import and_, select
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlbot_xpack.config.model import SysArgModel
from sqlbot_xpack.custom_prompt.curd.custom_prompt import find_custom_prompts
from sqlbot_xpack.custom_prompt.models.custom_prompt_model import CustomPromptTypeEnum
from sqlbot_xpack.license.license_manage import SQLBotLicenseUtil
from sqlmodel import Session

from apps.access_control.data_policy import requires_data_policy, resolve_data_policy
from apps.ai_model.model_factory import LLMConfig, LLMFactory, get_default_config
from apps.assistant import AssistantOutDsSchema
from apps.assistant.public import (
    AssistantOutDs,
    AssistantOutDsFactory,
    get_assistant_ds,
)
from apps.chat.composition import build_conversation_service
from apps.chat.curd.chat import (
    end_log,
    finish_record,
    format_chart_fields,
    format_json_data,
    get_chart_config,
    get_chat_brief_generate,
    get_chat_chart_config,
    get_chat_chart_data,
    get_chat_predict_data,
    get_last_execute_sql_error,
    list_generate_chart_logs,
    list_generate_sql_logs,
    save_analysis_predict_record,
    save_error_message,
    save_predict_data,
    save_sql,
    start_log,
    trigger_log_error,
)
from apps.chat.models.chat_model import (
    Chat,
    ChatFinishStep,
    ChatLog,
    ChatRecord,
    OperationEnum,
    RenameChat,
)
from apps.chat.services.semantic_binding import DYNAMIC_DATASOURCE_ASSISTANT_TYPES
from apps.chat.services.term_context import ChatTermContextService
from apps.chat.task.legacy_adapter import (
    build_context_prompt_log,
    build_role_prompt_log,
    encode_sse_event,
)
from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    ChartGenerationData,
    ChartGenerationMessage,
    ChatQuestion,
    ChatRecordAuxiliaryType,
    DatasourceSelectionCandidate,
    DatasourceSelectionData,
    DatasourceSelectionEvent,
    DynamicSQLGenerationData,
    DynamicSQLSubqueryMapping,
    GenerationAssistantContext,
    GenerationContextScope,
    GenerationContextScopeData,
    GenerationHistoryLog,
    GenerationHistoryProjectionData,
    PermissionSQLFilter,
    PermissionSQLGenerationData,
    QueryResultProjectionData,
    RecommendedQuestionGenerationData,
    SQLGenerationData,
    SQLGenerationMessage,
)
from apps.chatbi.services import (
    ChartGenerationError,
    DatasourceSelectionError,
    DynamicSQLGenerationError,
    GenerationContextScopeService,
    GenerationHistoryProjectionService,
    PermissionSQLGenerationError,
    SQLGenerationError,
)
from apps.datasource import (
    DatasourceConnection,
    build_external_datasource_connection,
)
from apps.datasource.crud.datasource import get_table_schema, get_tables_sample_data
from apps.datasource.database import check_connection, get_version
from apps.datasource.embedding.ds_embedding import get_ds_embedding
from apps.datasource.models.datasource import CoreDatasource
from apps.knowledge.composition import build_sql_example_query_service
from apps.semantic.composition import build_semantic_term_query_service
from apps.system.crud.parameter_manage import get_groups
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentAssistant, CurrentUser
from common.error import (
    ParseSQLResultError,
    SingleMessageError,
    SQLBotDBConnectionError,
    SQLBotDBError,
)
from common.utils.data_format import DataFormat
from common.utils.data_format_schema import AxisObj
from common.utils.locale import I18n, I18nHelper
from common.utils.utils import SQLBotLogUtil, extract_nested_json
from infrastructure.analysis_prediction import build_analysis_prediction_service
from infrastructure.chart_generation import build_chart_generation_service
from infrastructure.datasource_selection import build_datasource_selection_service
from infrastructure.dynamic_sql_generation import (
    build_dynamic_sql_generation_service,
)
from infrastructure.permission_sql_generation import (
    build_permission_sql_generation_service,
)
from infrastructure.query_execution import build_legacy_chat_query_service
from infrastructure.query_result_projection import (
    build_query_result_projection_service,
)
from infrastructure.recommended_questions import (
    build_recommended_question_service,
)
from infrastructure.sql_generation import build_sql_generation_service

warnings.filterwarnings("ignore")

executor = ThreadPoolExecutor(max_workers=200)

dynamic_subsql_prefix = 'select * from sqlbot_dynamic_temp_table_'

session_maker = scoped_session(sessionmaker(bind=engine, class_=Session))

i18n = I18n()


class LLMService:
    ds: CoreDatasource | AssistantOutDsSchema | None
    connection: DatasourceConnection | None
    chat_question: ChatQuestion
    record: ChatRecord
    config: LLMConfig
    llm: BaseChatModel
    sql_history: list[SQLGenerationMessage]
    chart_history: list[ChartGenerationMessage]

    # session: Session = db_session
    current_user: CurrentUser
    chat_oid: int
    current_assistant: Optional[CurrentAssistant] = None
    out_ds_instance: Optional[AssistantOutDs] = None
    change_title: bool = False

    generate_sql_logs: List[ChatLog]
    generate_chart_logs: List[ChatLog]
    current_logs: dict[OperationEnum, ChatLog]
    chunk_list: List[str]
    future: Future

    trans: I18nHelper = None

    last_execute_sql_error: str = None
    articles_number: int = 4

    enable_sql_row_limit: bool = settings.GENERATE_SQL_QUERY_LIMIT_ENABLED
    base_message_round_count_limit: int = settings.GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT

    def __init__(self, session: Session, current_user: CurrentUser, chat_question: ChatQuestion,
                 current_assistant: Optional[CurrentAssistant] = None, no_reasoning: bool = False,
                 embedding: bool = False, config: LLMConfig = None):
        self.sql_history = []
        self.chart_history = []
        self.generate_sql_logs = []
        self.generate_chart_logs = []
        self.current_logs = {}
        self.chunk_list = []
        self.current_user = current_user
        self.current_assistant = current_assistant
        chat_id = chat_question.chat_id
        chat: Chat | None = session.get(Chat, chat_id)
        if not chat:
            raise SingleMessageError(f"Chat with id {chat_id} not found")
        self.chat_oid = chat.oid or current_user.oid or 1
        ds: CoreDatasource | AssistantOutDsSchema | None = None
        connection: DatasourceConnection | None = None
        if not chat.datasource and chat_question.datasource_id:
            _ds = session.get(CoreDatasource, chat_question.datasource_id)
            if _ds:
                if _ds.oid != current_user.oid:
                    raise SingleMessageError(
                        f"Datasource with id {chat_question.datasource_id} does not belong to current workspace")
                chat.datasource = _ds.id
                chat.engine_type = _ds.type_name
                # save chat
                session.add(chat)
                session.flush()
                session.refresh(chat)
                session.commit()

        if chat.datasource:
            # Get available datasource
            if (
                current_assistant
                and current_assistant.type in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
            ):
                self.out_ds_instance = AssistantOutDsFactory.get_instance(current_assistant)
                ds = self.out_ds_instance.get_ds(chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                connection = build_external_datasource_connection(ds, 10)
                chat_question.engine = connection.type + get_version(connection)
            else:
                ds = session.get(CoreDatasource, chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                connection = DatasourceConnection.model_validate(ds)
                chat_question.engine = (ds.type_name if ds.type != 'excel' else 'PostgreSQL') + get_version(connection)

        self.generate_sql_logs = list_generate_sql_logs(session=session, chart_id=chat_id)
        self.generate_chart_logs = list_generate_chart_logs(session=session, chart_id=chat_id)

        self.change_title = not get_chat_brief_generate(session=session, chat_id=chat_id)

        chat_question.lang = get_lang_name(current_user.language)
        self.trans = i18n(lang=current_user.language)

        self.ds = (
            ds if isinstance(ds, AssistantOutDsSchema) else CoreDatasource(**ds.model_dump())) if ds else None
        self.connection = connection
        self.chat_question = chat_question
        self.config = config
        if no_reasoning:
            # only work while using qwen
            if self.config.additional_params:
                if self.config.additional_params.get('extra_body'):
                    if self.config.additional_params.get('extra_body').get('enable_thinking'):
                        del self.config.additional_params['extra_body']['enable_thinking']

        self.chat_question.ai_modal_id = self.config.model_id
        self.chat_question.ai_modal_name = self.config.model_name

        # Create LLM instance through factory
        llm_instance = LLMFactory.create_llm(self.config)
        self.llm = llm_instance.llm

        # get last_execute_sql_error
        last_execute_sql_error = get_last_execute_sql_error(session, self.chat_question.chat_id)
        if last_execute_sql_error:
            self.chat_question.error_msg = f'''<error-msg>
{last_execute_sql_error}
</error-msg>'''
        else:
            self.chat_question.error_msg = ''

    @classmethod
    async def create(cls, *args, **kwargs):
        specialized_model_id = None
        if args[3]:
            if args[3].enable_custom_model:
                if args[3].custom_model:
                    specialized_model_id = args[3].custom_model
                    print("use custom model: id[" + args[3].custom_model + "]")
        config: LLMConfig = await get_default_config(specialized_model_id)
        instance = cls(*args, **kwargs, config=config)

        chat_params: list[SysArgModel] = await get_groups(args[0], "chat")
        for config in chat_params:
            if config.pkey == 'chat.sqlbot_name':
                if config.pval.strip():
                    instance.chat_question.sqlbot_name = config.pval
            if config.pkey == 'chat.limit_rows':
                if config.pval.lower().strip() == 'true':
                    instance.enable_sql_row_limit = True
                else:
                    instance.enable_sql_row_limit = False
            if config.pkey == 'chat.context_record_count':
                count_value = config.pval
                if count_value is None:
                    count_value = settings.GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT
                count_value = int(count_value)
                if count_value < 0:
                    count_value = 0
                instance.base_message_round_count_limit = count_value
        return instance

    def is_running(self, timeout=0.5):
        try:
            r = concurrent.futures.wait([self.future], timeout)
            if len(r.not_done) > 0:
                return True
            else:
                return False
        except Exception:
            return True

    def init_messages(self, session: Session):
        self.choose_table_schema(session)
        projection = GenerationHistoryProjectionService().project(
            GenerationHistoryProjectionData(
                sql_logs=[
                    GenerationHistoryLog(
                        record_id=log.pid,
                        messages=log.messages,
                    )
                    for log in self.generate_sql_logs
                ],
                chart_logs=[
                    GenerationHistoryLog(
                        record_id=log.pid,
                        messages=log.messages,
                    )
                    for log in self.generate_chart_logs
                ],
                regenerate_record_id=self.chat_question.regenerate_record_id,
                round_limit=self.base_message_round_count_limit,
            )
        )
        self.sql_history = projection.sql_history
        self.chart_history = projection.chart_history

    def get_record(self):
        return self.record

    def set_record(self, record: ChatRecord):
        self.record = record

    def set_articles_number(self, articles_number: int):
        self.articles_number = articles_number

    def get_fields_from_chart(self, _session: Session):
        chart_info = get_chart_config(_session, self.record.id)
        return format_chart_fields(chart_info)

    def load_term_context(self, _session: Session):
        self.current_logs[OperationEnum.FILTER_TERMS] = start_log(session=_session,
                                                                  operate=OperationEnum.FILTER_TERMS,
                                                                  record_id=self.record.id, local_operation=True)

        if self.record.dataset_id is None:
            self.chat_question.terminologies = ""
            term_list = []
        else:
            term_context_service = ChatTermContextService(
                build_semantic_term_query_service(_session)
            )
            self.chat_question.terminologies, term_list = term_context_service.build(
                self.chat_oid,
                self.record.dataset_id,
                self.chat_question.question,
            )
        self.current_logs[OperationEnum.FILTER_TERMS] = end_log(session=_session,
                                                                log=self.current_logs[OperationEnum.FILTER_TERMS],
                                                                full_message=term_list)

    def resolve_generation_context_scope(
            self,
            workspace_id: int | None,
            datasource_id: int | None,
    ) -> GenerationContextScope:
        assistant = None
        if self.current_assistant:
            assistant = GenerationAssistantContext(
                assistant_id=self.current_assistant.id,
                workspace_id=self.current_assistant.oid,
                assistant_type=self.current_assistant.type,
            )
        return GenerationContextScopeService().project(
            GenerationContextScopeData(
                default_workspace_id=workspace_id,
                current_user_workspace_id=self.current_user.oid,
                datasource_id=datasource_id,
                assistant=assistant,
            )
        )

    def filter_custom_prompts(self, _session: Session, custom_prompt_type: CustomPromptTypeEnum, oid: int = None,
                              ds_id: int = None):
        if SQLBotLicenseUtil.valid():
            scope = self.resolve_generation_context_scope(oid, ds_id)
            self.current_logs[OperationEnum.FILTER_CUSTOM_PROMPT] = start_log(session=_session,
                                                                              operate=OperationEnum.FILTER_CUSTOM_PROMPT,
                                                                              record_id=self.record.id,
                                                                              local_operation=True)
            self.chat_question.custom_prompt, prompt_list = find_custom_prompts(_session, custom_prompt_type,
                                                                                scope.workspace_id,
                                                                                scope.datasource_id)
            self.current_logs[OperationEnum.FILTER_CUSTOM_PROMPT] = end_log(session=_session,
                                                                            log=self.current_logs[
                                                                                OperationEnum.FILTER_CUSTOM_PROMPT],
                                                                            full_message=prompt_list)

    def filter_training_template(self, _session: Session, oid: int = None, ds_id: int = None):
        self.current_logs[OperationEnum.FILTER_SQL_EXAMPLE] = start_log(session=_session,
                                                                        operate=OperationEnum.FILTER_SQL_EXAMPLE,
                                                                        record_id=self.record.id,
                                                                        local_operation=True)
        scope = self.resolve_generation_context_scope(oid, ds_id)
        if scope.use_assistant_sql_examples:
            self.chat_question.data_training, example_list = (
                build_sql_example_query_service(_session).build_prompt(
                    self.chat_question.question,
                    scope.workspace_id,
                    assistant_id=scope.sql_example_assistant_id,
                )
            )
        else:
            self.chat_question.data_training, example_list = (
                build_sql_example_query_service(_session).build_prompt(
                    self.chat_question.question,
                    scope.workspace_id,
                    datasource_id=scope.datasource_id,
                )
            )
        self.current_logs[OperationEnum.FILTER_SQL_EXAMPLE] = end_log(session=_session,
                                                                      log=self.current_logs[
                                                                          OperationEnum.FILTER_SQL_EXAMPLE],
                                                                      full_message=example_list)

    def choose_table_schema(self, _session: Session):
        self.current_logs[OperationEnum.CHOOSE_TABLE] = start_log(session=_session,
                                                                  operate=OperationEnum.CHOOSE_TABLE,
                                                                  record_id=self.record.id,
                                                                  local_operation=True)
        self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
            self.ds.id, self.chat_question.question) if self.out_ds_instance else get_table_schema(
            session=_session,
            current_user=self.current_user,
            ds=self.ds,
            question=self.chat_question.question)

        # Get sample data for all tables
        if not self.out_ds_instance:
            self.chat_question.sample_data = get_tables_sample_data(
                session=_session,
                current_user=self.current_user,
                ds=self.ds)

        self.current_logs[OperationEnum.CHOOSE_TABLE] = end_log(session=_session,
                                                                log=self.current_logs[OperationEnum.CHOOSE_TABLE],
                                                                full_message=self.chat_question.db_schema)

    def generate_analysis(self, _session: Session):
        fields = self.get_fields_from_chart(_session)
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(_session, self.record.id)
        self.chat_question.data = orjson.dumps(data.get('data')).decode()
        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

        self.load_term_context(_session)

        self.filter_custom_prompts(_session, CustomPromptTypeEnum.ANALYSIS, self.current_user.oid, ds_id)

        generation_data = AnalysisPredictionGenerationData(
            record_id=self.record.id or 0,
            generation_type=ChatRecordAuxiliaryType.ANALYSIS,
            fields=self.chat_question.fields,
            data=self.chat_question.data,
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            custom_prompt=self.chat_question.custom_prompt,
            terminologies=self.chat_question.terminologies,
        )
        service = build_analysis_prediction_service(_session, self.llm)
        messages = service.prepare(generation_data)

        self.current_logs[OperationEnum.ANALYSIS] = start_log(session=_session,
                                                              ai_modal_id=self.chat_question.ai_modal_id,
                                                              ai_modal_name=self.chat_question.ai_modal_name,
                                                              operate=OperationEnum.ANALYSIS,
                                                              record_id=self.record.id,
                                                              full_message=build_role_prompt_log(messages))
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                yield {
                    'content': event.content,
                    'reasoning_content': event.reasoning_content,
                }
                continue

            self.current_logs[OperationEnum.ANALYSIS] = end_log(
                session=_session,
                log=self.current_logs[OperationEnum.ANALYSIS],
                full_message=build_role_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )

    def generate_predict(self, _session: Session):
        fields = self.get_fields_from_chart(_session)
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(_session, self.record.id)
        self.chat_question.data = orjson.dumps(data.get('data')).decode()

        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
        self.filter_custom_prompts(_session, CustomPromptTypeEnum.PREDICT_DATA, self.current_user.oid, ds_id)

        generation_data = AnalysisPredictionGenerationData(
            record_id=self.record.id or 0,
            generation_type=ChatRecordAuxiliaryType.PREDICT,
            fields=self.chat_question.fields,
            data=self.chat_question.data,
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            custom_prompt=self.chat_question.custom_prompt,
        )
        service = build_analysis_prediction_service(_session, self.llm)
        messages = service.prepare(generation_data)

        self.current_logs[OperationEnum.PREDICT_DATA] = start_log(session=_session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.PREDICT_DATA,
                                                                  record_id=self.record.id,
                                                                  full_message=build_role_prompt_log(messages))
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                yield {
                    'content': event.content,
                    'reasoning_content': event.reasoning_content,
                }
                continue

            self.current_logs[OperationEnum.PREDICT_DATA] = end_log(
                session=_session,
                log=self.current_logs[OperationEnum.PREDICT_DATA],
                full_message=build_role_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )

    def generate_recommend_questions_task(self, _session: Session):

        # get schema
        if self.ds and not self.chat_question.db_schema:
            self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
                self.ds.id, self.chat_question.question) if self.out_ds_instance else get_table_schema(
                session=_session,
                current_user=self.current_user, ds=self.ds,
                question=self.chat_question.question,
                embedding=False)

            # Get sample data for all tables
            if not self.out_ds_instance:
                self.chat_question.sample_data = get_tables_sample_data(
                    session=_session,
                    current_user=self.current_user,
                    ds=self.ds)

        data = RecommendedQuestionGenerationData(
            record_id=self.record.id or 0,
            question=self.chat_question.question or "",
            schema=self.chat_question.db_schema or "",
            datasource_id=self.record.datasource,
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            articles_number=self.articles_number,
        )
        service = build_recommended_question_service(_session, self.llm)
        messages = service.prepare(data)

        self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = start_log(session=_session,
                                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                                    operate=OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
                                                                                    record_id=self.record.id,
                                                                                    full_message=build_role_prompt_log(
                                                                                        messages))
        for event in service.generate(data, messages):
            if event.kind == 'chunk':
                yield {
                    'content': event.content,
                    'reasoning_content': event.reasoning_content,
                }
                continue

            self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = end_log(
                session=_session,
                log=self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS],
                full_message=build_role_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
            yield {'recommended_question': event.recommended_question}

    def select_datasource(self, _session: Session):
        if self.current_assistant and self.current_assistant.type != 4:
            _ds_list = get_assistant_ds(session=_session, llm_service=self)
        else:
            stmt = select(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description).where(
                and_(CoreDatasource.oid == self.current_user.oid))
            _ds_list = [
                {
                    "id": ds.id,
                    "name": ds.name,
                    "description": ds.description
                }
                for ds in _session.exec(stmt)
            ]
        auto_select = len(_ds_list) == 1
        if not auto_select:
            if settings.TABLE_EMBEDDING_ENABLED and (
                    not self.current_assistant or (self.current_assistant and self.current_assistant.type != 1)):
                _ds_list = get_ds_embedding(_session, self.current_user, _ds_list, self.out_ds_instance,
                                            self.chat_question.question, self.current_assistant)

        selection_data = DatasourceSelectionData(
            record_id=self.record.id or 0,
            question=self.chat_question.question or "",
            candidates=[
                DatasourceSelectionCandidate(
                    id=cast(int, candidate.get("id")),
                    name=cast(str, candidate.get("name")),
                    description=cast(str | None, candidate.get("description")),
                )
                for candidate in _ds_list
            ],
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            auto_select=auto_select,
        )
        service = build_datasource_selection_service(_session, self.llm)
        try:
            messages = service.prepare(selection_data)
        except DatasourceSelectionError as exc:
            raise SingleMessageError(str(exc)) from exc

        if not auto_select:
            self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = start_log(session=_session,
                                                                           ai_modal_id=self.chat_question.ai_modal_id,
                                                                           ai_modal_name=self.chat_question.ai_modal_name,
                                                                           operate=OperationEnum.CHOOSE_DATASOURCE,
                                                                           record_id=self.record.id,
                                                                           full_message=build_role_prompt_log(
                                                                               messages))

        selection_event: DatasourceSelectionEvent | None = None
        for event in service.generate(selection_data, messages):
            if event.kind == 'chunk':
                yield {
                    'content': event.content,
                    'reasoning_content': event.reasoning_content,
                }
                continue
            selection_event = event

        if not auto_select:
            if selection_event is None:
                raise SingleMessageError('DATASOURCE_SELECTION_RESULT_REQUIRED')

            self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = end_log(session=_session,
                                                                         log=self.current_logs[
                                                                             OperationEnum.CHOOSE_DATASOURCE],
                                                                         full_message=build_role_prompt_log(
                                                                             messages,
                                                                             selection_event.content,
                                                                         ),
                                                                         reasoning_content=selection_event.reasoning_content,
                                                                         token_usage=selection_event.token_usage)

        if selection_event is None:
            raise SingleMessageError('DATASOURCE_SELECTION_RESULT_REQUIRED')
        if selection_event.error:
            raise SingleMessageError(selection_event.error)
        selected_datasource_id = selection_event.selected_datasource_id
        if selected_datasource_id is None:
            raise SingleMessageError('DATASOURCE_SELECTION_RESULT_REQUIRED')

        if (
            self.current_assistant
            and self.current_assistant.type in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
        ):
            _ds = self.out_ds_instance.get_ds(selected_datasource_id)
            self.ds = _ds
            self.connection = build_external_datasource_connection(_ds, 10)
            self.chat_question.engine = self.connection.type + get_version(
                self.connection
            )
            conversation_engine_type = _ds.type
        else:
            _ds = _session.get(CoreDatasource, selected_datasource_id)
            if not _ds:
                raise SingleMessageError(
                    f"Datasource configuration with id {selected_datasource_id} not found"
                )
            self.ds = CoreDatasource(**_ds.model_dump())
            self.connection = DatasourceConnection.model_validate(_ds)
            self.chat_question.engine = (
                _ds.type_name if _ds.type != 'excel' else 'PostgreSQL'
            ) + get_version(self.connection)
            conversation_engine_type = _ds.type_name

        try:
            self.record = service.bind_selection(
                selection_data,
                selection_event,
                record_engine_type=self.chat_question.engine,
                conversation_engine_type=conversation_engine_type,
            )
            _session.commit()
        except Exception:
            # 会话与记录必须在同一事务中完成绑定。
            _session.rollback()
            raise

        if self.ds:
            oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
            ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

            self.load_term_context(_session)

            self.filter_training_template(_session, oid, ds_id)

            self.filter_custom_prompts(_session, CustomPromptTypeEnum.GENERATE_SQL, oid, ds_id)

            self.init_messages(_session)

    def generate_sql(self, _session: Session):
        generation_data = SQLGenerationData(
            record_id=self.record.id or 0,
            question=self.chat_question.question or "",
            database_type=self.ds.type,
            engine=self.chat_question.engine,
            schema=self.chat_question.db_schema,
            sample_data=self.chat_question.sample_data,
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            rule=self.chat_question.rule,
            error_message=self.chat_question.error_msg,
            custom_prompt=self.chat_question.custom_prompt,
            terminologies=self.chat_question.terminologies,
            data_training=self.chat_question.data_training,
            enable_query_limit=self.enable_sql_row_limit,
            change_title=self.change_title,
            regenerate=self.chat_question.regenerate_record_id is not None,
            history=list(self.sql_history),
        )
        service = build_sql_generation_service(_session, self.llm)
        try:
            messages = service.prepare(generation_data)
        except SQLGenerationError as exc:
            raise SingleMessageError(str(exc)) from exc

        self.current_logs[OperationEnum.GENERATE_SQL] = start_log(session=_session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.GENERATE_SQL,
                                                                  record_id=self.record.id,
                                                                  full_message=build_context_prompt_log(messages))
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                yield event
                continue

            self.current_logs[OperationEnum.GENERATE_SQL] = end_log(
                session=_session,
                log=self.current_logs[OperationEnum.GENERATE_SQL],
                full_message=build_context_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
            yield event

    def generate_with_sub_sql(
            self,
            session: Session,
            sql: str,
            sub_mappings: list[DynamicSQLSubqueryMapping],
    ) -> str:
        generation_data = DynamicSQLGenerationData(
            sql=sql,
            subqueries=sub_mappings,
            language=self.chat_question.lang,
            engine=self.chat_question.engine,
            assistant_name=self.chat_question.sqlbot_name,
        )
        service = build_dynamic_sql_generation_service(self.llm)
        try:
            messages = service.prepare(generation_data)
        except DynamicSQLGenerationError as exc:
            raise SingleMessageError(str(exc)) from exc

        self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = start_log(session=session,
                                                                          ai_modal_id=self.chat_question.ai_modal_id,
                                                                          ai_modal_name=self.chat_question.ai_modal_name,
                                                                          operate=OperationEnum.GENERATE_DYNAMIC_SQL,
                                                                          record_id=self.record.id,
                                                                          full_message=build_context_prompt_log(
                                                                              messages))
        dynamic_result = None
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                continue
            self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = end_log(
                session=session,
                log=self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL],
                full_message=build_context_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
            SQLBotLogUtil.info(event.content)
            if event.error:
                trigger_log_error(
                    session,
                    self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL],
                )
                raise SingleMessageError(event.error)
            dynamic_result = event.result

        if dynamic_result is None:
            raise SingleMessageError('DYNAMIC_SQL_GENERATION_RESULT_REQUIRED')
        return dynamic_result.sql

    def generate_assistant_dynamic_sql(self, _session: Session, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        sub_query: list[DynamicSQLSubqueryMapping] = []
        result_dict = {}
        for table in ds.tables:
            if table.name in tables and table.sql:
                # sub_query.append({"table": table.name, "query": table.sql})
                result_dict[table.name] = table.sql
                sub_query.append(
                    DynamicSQLSubqueryMapping(
                        table=table.name,
                        query=f'{dynamic_subsql_prefix}{table.name}',
                    )
                )
        if not sub_query:
            return None
        temp_sql_text = self.generate_with_sub_sql(session=_session, sql=sql, sub_mappings=sub_query)
        result_dict['sqlbot_temp_sql_text'] = temp_sql_text
        return result_dict

    def build_table_filter(
            self,
            session: Session,
            sql: str,
            filters: list[PermissionSQLFilter],
    ) -> str:
        generation_data = PermissionSQLGenerationData(
            record_id=self.record.id or 0,
            sql=sql,
            filters=filters,
            language=self.chat_question.lang,
            engine=self.chat_question.engine,
            assistant_name=self.chat_question.sqlbot_name,
        )
        service = build_permission_sql_generation_service(session, self.llm)
        try:
            messages = service.prepare(generation_data)
        except PermissionSQLGenerationError as exc:
            raise SingleMessageError(str(exc)) from exc

        self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = start_log(session=session,
                                                                                   ai_modal_id=self.chat_question.ai_modal_id,
                                                                                   ai_modal_name=self.chat_question.ai_modal_name,
                                                                                   operate=OperationEnum.GENERATE_SQL_WITH_PERMISSIONS,
                                                                                   record_id=self.record.id,
                                                                                   full_message=build_context_prompt_log(
                                                                                       messages))
        permission_result = None
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                continue
            self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = end_log(
                session=session,
                log=self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS],
                full_message=build_context_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
            SQLBotLogUtil.info(event.content)
            if event.error:
                trigger_log_error(
                    session,
                    self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS],
                )
                raise SingleMessageError(event.error)
            permission_result = event.result

        if permission_result is None:
            raise SingleMessageError('PERMISSION_SQL_GENERATION_RESULT_REQUIRED')
        self.chat_question.sql = permission_result.sql
        return permission_result.sql

    def generate_filter(self, _session: Session, sql: str, tables: List):
        # 行权限只通过 Access Control 的公开数据策略解析。
        policy = resolve_data_policy(
            _session,
            self.current_user,
            self.ds.id,
            table_names=tables,
        )
        filters = [
            PermissionSQLFilter(table=item.table, condition=item.condition)
            for item in policy.row_filters
        ]
        if not filters:
            return None
        return self.build_table_filter(session=_session, sql=sql, filters=filters)

    def generate_assistant_filter(self, _session: Session, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        filters: list[PermissionSQLFilter] = []
        for table in ds.tables:
            if table.name in tables and table.rule:
                filters.append(
                    PermissionSQLFilter(
                        table=table.name,
                        condition=table.rule,
                    )
                )
        if not filters:
            return None
        return self.build_table_filter(session=_session, sql=sql, filters=filters)

    def generate_chart(self, _session: Session, chart_type: Optional[str] = '', schema: Optional[str] = ''):
        generation_data = ChartGenerationData(
            record_id=self.record.id or 0,
            question=self.chat_question.question or "",
            sql=self.chat_question.sql,
            schema=schema or "",
            chart_type=chart_type or "",
            language=self.chat_question.lang,
            assistant_name=self.chat_question.sqlbot_name,
            rule=self.chat_question.rule,
            history=list(self.chart_history),
        )
        service = build_chart_generation_service(_session, self.llm)
        try:
            messages = service.prepare(generation_data)
        except ChartGenerationError as exc:
            raise SingleMessageError(str(exc)) from exc

        self.current_logs[OperationEnum.GENERATE_CHART] = start_log(session=_session,
                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                    operate=OperationEnum.GENERATE_CHART,
                                                                    record_id=self.record.id,
                                                                    full_message=build_context_prompt_log(messages))
        for event in service.generate(generation_data, messages):
            if event.kind == 'chunk':
                yield event
                continue

            self.current_logs[OperationEnum.GENERATE_CHART] = end_log(
                session=_session,
                log=self.current_logs[OperationEnum.GENERATE_CHART],
                full_message=build_context_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
            yield event

    def check_save_predict_data(self, session: Session, res: str) -> bool:

        json_str = extract_nested_json(res)

        if not json_str:
            json_str = ''

        save_predict_data(session=session, record_id=self.record.id, data=json_str)

        if json_str == '':
            return False

        return True

    def save_error(self, session: Session, message: str):
        return save_error_message(session=session, record_id=self.record.id, message=message)

    def finish(self, session: Session):
        return finish_record(session=session, record_id=self.record.id)

    def execute_sql(
            self,
            sql: str,
            *,
            allowed_tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """通过 ChatBI 统一查询入口执行旧 Chat SQL。"""
        if self.connection is None or self.connection.id is None:
            raise SQLBotDBError("Datasource connection is not initialized")
        SQLBotLogUtil.info(
            f"Executing SQL on ds_id {self.connection.id}: {sql}"
        )
        query_result = build_legacy_chat_query_service(
            self.connection,
            enable_query_limit=self.enable_sql_row_limit,
        ).execute_sql(
            sql=sql,
            datasource_id=self.connection.id,
            workspace_id=self.current_user.oid,
            user_id=self.current_user.id,
            allowed_tables=allowed_tables,
        )
        if not query_result.success:
            message = query_result.message or query_result.error_code or "SQL 执行失败"
            if query_result.error_code == "sql_result_parse_error":
                raise ParseSQLResultError(message)
            raise SQLBotDBError(message)

        payload = query_result.payload
        metadata = payload.get("execution_metadata")
        result = dict(metadata) if isinstance(metadata, dict) else {}
        result["fields"] = payload.get("fields") or []
        result["data"] = payload.get("full_data") or []
        return result

    def pop_chunk(self):
        try:
            chunk = self.chunk_list.pop(0)
            return chunk
        except IndexError:
            return None

    def await_result(self):
        while self.is_running():
            while True:
                chunk = self.pop_chunk()
                if chunk is not None:
                    yield chunk
                else:
                    break
        while True:
            chunk = self.pop_chunk()
            if chunk is None:
                break
            yield chunk

    def run_task_async(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART, return_img: bool = True):
        if in_chat:
            stream = True
        self.future = executor.submit(self.run_task_cache, in_chat, stream, finish_step, return_img)

    def run_task_cache(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART, return_img: bool = True):
        for chunk in self.run_task(in_chat, stream, finish_step, return_img):
            self.chunk_list.append(chunk)

    def run_task(self, in_chat: bool = True, stream: bool = True,
                 finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART, return_img: bool = True):
        json_result: Dict[str, Any] = {'success': True}
        _session = None
        run_failed = False
        try:
            _session = session_maker()
            if self.ds:
                oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
                ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

                self.load_term_context(_session)

                self.filter_training_template(_session, oid, ds_id)

                self.filter_custom_prompts(_session, CustomPromptTypeEnum.GENERATE_SQL, oid, ds_id)

                self.init_messages(_session)

            # return id
            if in_chat:
                yield encode_sse_event('id', id=self.get_record().id)
                if self.get_record().regenerate_record_id:
                    yield encode_sse_event(
                        'regenerate_record_id',
                        regenerate_record_id=self.get_record().regenerate_record_id,
                    )
                yield encode_sse_event('question', question=self.get_record().question)
            else:
                if stream:
                    yield '> ' + self.trans('i18n_chat.record_id_in_mcp') + str(self.get_record().id) + '\n'
                    yield '> ' + self.get_record().question + '\n\n'
            if not stream:
                json_result['record_id'] = self.get_record().id

                # select datasource if datasource is none
            if not self.ds:
                ds_res = self.select_datasource(_session)

                for chunk in ds_res:
                    SQLBotLogUtil.info(chunk)
                    if in_chat:
                        yield encode_sse_event(
                            'datasource-result',
                            content=chunk.get('content'),
                            reasoning_content=chunk.get('reasoning_content'),
                        )
                if in_chat:
                    yield encode_sse_event(
                        'datasource',
                        id=self.ds.id,
                        datasource_name=self.ds.name,
                        engine_type=self.ds.type_name or self.ds.type,
                    )

            else:
                self.validate_history_ds(_session)

            # check connection
            if self.connection is None:
                raise SQLBotDBConnectionError("Datasource connection is not initialized")
            connected = check_connection(ds=self.connection, trans=None)
            if not connected:
                raise SQLBotDBConnectionError('Connect DB failed')

            # generate sql
            sql_res = self.generate_sql(_session)
            full_sql_text = ''
            sql_generation_result = None
            for event in sql_res:
                if event.kind == 'chunk':
                    full_sql_text += event.content
                    if in_chat:
                        yield encode_sse_event(
                            'sql-result',
                            content=event.content,
                            reasoning_content=event.reasoning_content,
                        )
                    continue
                if event.error:
                    trigger_log_error(_session, self.current_logs[OperationEnum.GENERATE_SQL])
                    raise SingleMessageError(event.error)
                sql_generation_result = event.result
            if in_chat:
                yield encode_sse_event('info', msg='sql generated')
            # filter sql
            SQLBotLogUtil.info(full_sql_text)

            if sql_generation_result is None:
                raise SingleMessageError('SQL_GENERATION_RESULT_REQUIRED')
            chart_type = sql_generation_result.chart_type

            # return title
            if self.change_title:
                llm_brief = sql_generation_result.brief
                llm_brief_generated = bool(llm_brief)
                if llm_brief_generated or (self.chat_question.question and self.chat_question.question.strip() != ''):
                    save_brief = llm_brief if (llm_brief and llm_brief != '') else self.chat_question.question.strip()[
                                                                                   :20]
                    brief = build_conversation_service(_session).rename(
                        self.current_user.id,
                        RenameChat(
                            id=self.get_record().chat_id,
                            brief=save_brief,
                            brief_generate=llm_brief_generated,
                        ),
                    )
                    if in_chat:
                        yield encode_sse_event('brief', brief=brief)
                    if not stream:
                        json_result['title'] = brief

            use_dynamic_ds: bool = (
                self.current_assistant
                and self.current_assistant.type
                in DYNAMIC_DATASOURCE_ASSISTANT_TYPES
            )
            is_page_embedded: bool = self.current_assistant and self.current_assistant.type == 4
            dynamic_sql_result = None
            sqlbot_temp_sql_text = None
            assistant_dynamic_sql = None
            # row permission

            sql = sql_generation_result.sql
            tables = sql_generation_result.tables
            if ((not self.current_assistant or is_page_embedded) and requires_data_policy(
                    self.current_user)) or use_dynamic_ds:
                sql_result = None

                if use_dynamic_ds:
                    dynamic_sql_result = self.generate_assistant_dynamic_sql(_session, sql, tables)
                    sqlbot_temp_sql_text = dynamic_sql_result.get(
                        'sqlbot_temp_sql_text') if dynamic_sql_result else None
                else:
                    sql_result = self.generate_filter(_session, sql, tables)  # maybe no sql and tables

                if sql_result:
                    SQLBotLogUtil.info(sql_result)
                    sql = sql_result
                elif dynamic_sql_result and sqlbot_temp_sql_text:
                    assistant_dynamic_sql = sqlbot_temp_sql_text
                    save_sql(
                        session=_session,
                        sql=assistant_dynamic_sql,
                        record_id=self.record.id,
                    )
                    self.chat_question.sql = assistant_dynamic_sql
                else:
                    save_sql(session=_session, sql=sql, record_id=self.record.id)
                    self.chat_question.sql = sql
            else:
                save_sql(session=_session, sql=sql, record_id=self.record.id)
                self.chat_question.sql = sql

            SQLBotLogUtil.info('sql: ' + sql)

            if not stream:
                json_result['sql'] = sql

            format_sql = sqlparse.format(sql, reindent=True)
            if in_chat:
                yield encode_sse_event('sql', content=format_sql)
            else:
                if stream:
                    yield f'```sql\n{format_sql}\n```\n\n'

            # execute sql
            real_execute_sql = sql
            if sqlbot_temp_sql_text and assistant_dynamic_sql:
                dynamic_sql_result.pop('sqlbot_temp_sql_text')
                for origin_table, subsql in dynamic_sql_result.items():
                    assistant_dynamic_sql = assistant_dynamic_sql.replace(f'{dynamic_subsql_prefix}{origin_table}',
                                                                          subsql)
                real_execute_sql = assistant_dynamic_sql

            if finish_step.value <= ChatFinishStep.GENERATE_SQL.value:
                if in_chat:
                    yield encode_sse_event('finish')
                if not stream:
                    yield json_result
                return

            self.current_logs[OperationEnum.EXECUTE_SQL] = start_log(session=_session,
                                                                     operate=OperationEnum.EXECUTE_SQL,
                                                                     record_id=self.record.id, local_operation=True)
            result = self.execute_sql(
                sql=real_execute_sql,
                allowed_tables=None if use_dynamic_ds else tables,
            )
            self.current_logs[OperationEnum.EXECUTE_SQL] = end_log(session=_session,
                                                                   log=self.current_logs[OperationEnum.EXECUTE_SQL],
                                                                   full_message={'sql': real_execute_sql,
                                                                                 'count': len(result.get('data'))})

            datasource_id = self.connection.id if self.connection else None
            if datasource_id is None:
                raise SQLBotDBError("Datasource connection is not initialized")
            execution_metadata = {
                key: value
                for key, value in result.items()
                if key not in {"fields", "data"}
            }
            try:
                result = build_query_result_projection_service(_session).project(
                    QueryResultProjectionData(
                        record_id=self.record.id or 0,
                        datasource_id=datasource_id,
                        fields=result.get("fields") or [],
                        rows=result.get("data") or [],
                        execution_metadata=execution_metadata,
                        enable_row_limit=self.enable_sql_row_limit,
                    )
                )
                _session.commit()
            except Exception:
                _session.rollback()
                raise
            _data = result.get("data") or []
            if in_chat:
                yield encode_sse_event('sql-data', content='execute-success')
            if not stream:
                json_result['data'] = get_chat_chart_data(_session, self.record.id)

            if finish_step.value <= ChatFinishStep.QUERY_DATA.value:
                if stream:
                    if in_chat:
                        yield encode_sse_event('finish')
                    else:
                        _column_list = []
                        for field in result.get('fields'):
                            _column_list.append(AxisObj(name=field, value=field))

                        md_data, _fields_list = DataFormat.convert_object_array_for_pandas(_column_list,
                                                                                           result.get('data'))

                        # data, _fields_list, col_formats = self.format_pd_data(_column_list, result.get('data'))

                        if not _data or not _fields_list:
                            yield 'The SQL execution result is empty.\n\n'
                        else:
                            df = pd.DataFrame(_data, columns=_fields_list)
                            df_safe = DataFormat.safe_convert_to_string(df)
                            markdown_table = df_safe.to_markdown(index=False)
                            yield markdown_table + '\n\n'
                else:
                    yield json_result
                return

            # generate chart
            used_tables_schema = self.out_ds_instance.get_db_schema(
                self.ds.id, self.chat_question.question, embedding=False,
                table_list=tables) if self.out_ds_instance else get_table_schema(
                session=_session,
                current_user=self.current_user,
                ds=self.ds,
                question=self.chat_question.question,
                embedding=False, table_list=tables)
            SQLBotLogUtil.info('used_tables_schema: \n' + used_tables_schema)
            chart_res = self.generate_chart(_session, chart_type, used_tables_schema)
            chart: dict[str, Any] | None = None
            for event in chart_res:
                if event.kind == 'chunk':
                    if in_chat:
                        yield encode_sse_event(
                            'chart-result',
                            content=event.content,
                            reasoning_content=event.reasoning_content,
                        )
                    continue
                if event.error:
                    raise SingleMessageError(event.error)
                chart = event.chart
            if in_chat:
                yield encode_sse_event('info', msg='chart generated')

            if chart is None:
                raise SingleMessageError('CHART_GENERATION_RESULT_REQUIRED')
            SQLBotLogUtil.info(chart)

            if not stream:
                json_result['chart'] = chart

            if in_chat:
                yield encode_sse_event('chart', content=orjson.dumps(chart).decode())
            else:
                if stream:
                    md_data, _fields_list = DataFormat.convert_data_fields_for_pandas(chart, result.get('fields'),
                                                                                      result.get('data'))
                    # data, _fields_list, col_formats = self.format_pd_data(_column_list, result.get('data'))

                    if not md_data or not _fields_list:
                        yield 'The SQL execution result is empty.\n\n'
                    else:
                        df = pd.DataFrame(md_data, columns=_fields_list)
                        df_safe = DataFormat.safe_convert_to_string(df)
                        markdown_table = df_safe.to_markdown(index=False)
                        yield markdown_table + '\n\n'

            if in_chat:
                yield encode_sse_event('finish')
            else:
                # generate picture
                try:
                    if chart.get('type') != 'table' and return_img:
                        # yield '### generated chart picture\n\n'
                        self.current_logs[OperationEnum.GENERATE_PICTURE] = start_log(session=_session,
                                                                                      operate=OperationEnum.GENERATE_PICTURE,
                                                                                      record_id=self.record.id,
                                                                                      local_operation=True)
                        image_url, error = request_picture(self.record.chat_id, self.record.id, chart,
                                                           format_json_data(result))
                        SQLBotLogUtil.info(image_url)
                        if stream:
                            yield f'![{chart.get("type")}]({image_url})'
                        else:
                            json_result['image_url'] = image_url
                        if error is not None:
                            raise error

                        self.current_logs[OperationEnum.GENERATE_PICTURE] = end_log(session=_session,
                                                                                    log=self.current_logs[
                                                                                        OperationEnum.GENERATE_PICTURE],
                                                                                    full_message=image_url)
                except Exception as e:
                    if stream:
                        if chart.get('type') != 'table':
                            yield 'generate or fetch chart picture error.\n\n'
                        raise e

            if not stream:
                yield json_result

        except Exception as e:
            run_failed = True
            traceback.print_exc()
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            elif isinstance(e, SQLBotDBConnectionError):
                error_msg = orjson.dumps(
                    {'message': str(e), 'type': 'db-connection-err'}).decode()
            elif isinstance(e, SQLBotDBError):
                error_msg = orjson.dumps(
                    {'message': 'Execute SQL Failed', 'traceback': str(e), 'type': 'exec-sql-err'}).decode()
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            if _session:
                self.save_error(session=_session, message=error_msg)
            if in_chat:
                yield encode_sse_event('error', content=error_msg)
            else:
                if stream:
                    yield f'&#x274c; **ERROR:**\n'
                    yield f'> {error_msg}\n'
                else:
                    json_result['success'] = False
                    json_result['message'] = error_msg
                    yield json_result
        finally:
            # 失败终态已经由 save_error 写入，不能再覆盖为成功。
            if _session and not run_failed:
                self.finish(_session)
            session_maker.remove()

    def run_recommend_questions_task_async(self):
        self.future = executor.submit(self.run_recommend_questions_task_cache)

    def run_recommend_questions_task_cache(self):
        for chunk in self.run_recommend_questions_task():
            self.chunk_list.append(chunk)

    def run_recommend_questions_task(self):
        try:
            _session = session_maker()
            res = self.generate_recommend_questions_task(_session)

            for chunk in res:
                if chunk.get('recommended_question'):
                    yield encode_sse_event(
                        'recommended_question',
                        content=chunk.get('recommended_question'),
                    )
                else:
                    yield encode_sse_event(
                        'recommended_question_result',
                        content=chunk.get('content'),
                        reasoning_content=chunk.get('reasoning_content'),
                    )
        except Exception:
            traceback.print_exc()
        finally:
            session_maker.remove()

    def run_analysis_or_predict_task_async(self, session: Session, action_type: str, base_record: ChatRecord,
                                           in_chat: bool = True, stream: bool = True):
        self.set_record(save_analysis_predict_record(session, base_record, action_type))
        self.future = executor.submit(self.run_analysis_or_predict_task_cache, action_type, in_chat, stream)

    def run_analysis_or_predict_task_cache(self, action_type: str, in_chat: bool = True, stream: bool = True):
        for chunk in self.run_analysis_or_predict_task(action_type, in_chat, stream):
            self.chunk_list.append(chunk)

    def run_analysis_or_predict_task(self, action_type: str, in_chat: bool = True, stream: bool = True):
        json_result: Dict[str, Any] = {'success': True}
        _session = None
        try:
            _session = session_maker()
            if in_chat:
                yield encode_sse_event('id', id=self.get_record().id)
            else:
                if stream:
                    yield '> ' + self.trans('i18n_chat.record_id_in_mcp') + str(self.get_record().id) + '\n'
                    yield '> ' + self.get_record().question + '\n\n'
            if not stream:
                json_result['record_id'] = self.get_record().id

            if action_type == 'analysis':
                # generate analysis
                analysis_res = self.generate_analysis(_session)
                full_text = ''
                for chunk in analysis_res:
                    full_text += chunk.get('content')
                    if in_chat:
                        yield encode_sse_event(
                            'analysis-result',
                            content=chunk.get('content'),
                            reasoning_content=chunk.get('reasoning_content'),
                        )
                    else:
                        if stream:
                            yield chunk.get('content')
                if in_chat:
                    yield encode_sse_event('info', msg='analysis generated')
                    yield encode_sse_event('analysis_finish')
                else:
                    if stream:
                        yield '\n\n'
                if not stream:
                    json_result['content'] = full_text

            elif action_type == 'predict':
                # generate predict
                analysis_res = self.generate_predict(_session)
                full_text = ''
                for chunk in analysis_res:
                    full_text += chunk.get('content')
                    if in_chat:
                        yield encode_sse_event(
                            'predict-result',
                            content=chunk.get('content'),
                            reasoning_content=chunk.get('reasoning_content'),
                        )
                if in_chat:
                    yield encode_sse_event('info', msg='predict generated')

                has_data = self.check_save_predict_data(session=_session, res=full_text)
                if has_data:
                    if in_chat:
                        yield encode_sse_event('predict-success')
                    else:
                        chart = get_chat_chart_config(_session, self.record.id)
                        origin_data = get_chat_chart_data(_session, self.record.id)
                        predict_data = get_chat_predict_data(_session, self.record.id)

                        if stream:
                            md_data, _fields_list = DataFormat.convert_data_fields_for_pandas(chart,
                                                                                              origin_data.get('fields'),
                                                                                              predict_data)
                            if not md_data or not _fields_list:
                                yield 'Predict data result is empty.\n\n'
                            else:
                                df = pd.DataFrame(md_data, columns=_fields_list)
                                df_safe = DataFormat.safe_convert_to_string(df)
                                markdown_table = df_safe.to_markdown(index=False)
                                yield markdown_table + '\n\n'

                        else:
                            json_result['origin_data'] = origin_data
                            json_result['predict_data'] = predict_data

                        # generate picture
                        try:
                            if chart.get('type') != 'table':
                                # yield '### generated chart picture\n\n'

                                _data = get_chat_chart_data(_session, self.record.id)
                                _data['data'] = _data.get('data') + predict_data

                                image_url, error = request_picture(self.record.chat_id, self.record.id, chart,
                                                                   format_json_data(_data))
                                SQLBotLogUtil.info(image_url)
                                if stream:
                                    yield f'![{chart.get("type")}]({image_url})'
                                else:
                                    json_result['image_url'] = image_url
                                if error is not None:
                                    raise error
                        except Exception as e:
                            if stream:
                                if chart.get('type') != 'table':
                                    yield 'generate or fetch chart picture error.\n\n'
                                raise e
                else:
                    if in_chat:
                        yield encode_sse_event('predict-failed')
                    else:
                        if stream:
                            yield full_text + '\n\n'
                    if not stream:
                        json_result['success'] = False
                        json_result['message'] = full_text
                if in_chat:
                    yield encode_sse_event('predict_finish')

            self.finish(_session)

            if not stream:
                yield json_result
        except Exception as e:
            traceback.print_exc()
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            if _session:
                self.save_error(session=_session, message=error_msg)
            if in_chat:
                yield encode_sse_event('error', content=error_msg)
            else:
                if stream:
                    yield f'&#x274c; **ERROR:**\n'
                    yield f'> {error_msg}\n'
                else:
                    json_result['success'] = False
                    json_result['message'] = error_msg
                    yield json_result
        finally:
            # end
            session_maker.remove()

    def validate_history_ds(self, session: Session):
        _ds = self.ds
        if not self.current_assistant or self.current_assistant.type == 4:
            try:
                current_ds = session.get(CoreDatasource, _ds.id)
                if not current_ds:
                    raise SingleMessageError('chat.ds_is_invalid')
            except Exception:
                raise SingleMessageError("chat.ds_is_invalid")
        else:
            try:
                _ds_list: list[dict] = get_assistant_ds(session=session, llm_service=self)
                match_ds = any(item.get("id") == _ds.id for item in _ds_list)
                if not match_ds:
                    type = self.current_assistant.type
                    msg = f"[please check ds list and public ds list]" if type == 0 else f"[please check ds api]"
                    raise SingleMessageError(msg)
            except Exception as e:
                raise SingleMessageError(f"ds is invalid [{str(e)}]")


def execute_sql_with_db(db: SQLDatabase, sql: str) -> str:
    """Execute SQL query using SQLDatabase

    Args:
        db: SQLDatabase instance
        sql: SQL query statement

    Returns:
        str: Query results formatted as string
    """
    try:
        # Execute query
        result = db.run(sql)

        if not result:
            return "Query executed successfully but returned no results."

        # Format results
        return str(result)

    except Exception as e:
        error_msg = f"SQL execution failed: {str(e)}"
        SQLBotLogUtil.exception(error_msg)
        raise RuntimeError(error_msg)


def request_picture(chat_id: int, record_id: int, chart: dict, data: dict):
    file_name = f'c_{chat_id}_r_{record_id}'

    columns = chart.get('columns') if chart.get('columns') else []
    x = None
    y = None
    series = None
    multi_quota_fields = []
    multi_quota_name = None

    if chart.get('axis'):
        axis_data = chart.get('axis')
        x = axis_data.get('x')
        y = axis_data.get('y')
        series = axis_data.get('series')
        # 获取multi-quota字段列表
        if axis_data.get('multi-quota') and 'value' in axis_data.get('multi-quota'):
            multi_quota_fields = axis_data.get('multi-quota').get('value', [])
            multi_quota_name = axis_data.get('multi-quota').get('name')

    axis = []
    for v in columns:
        axis.append({'name': v.get('name'), 'value': v.get('value')})
    if x:
        axis.append({'name': x.get('name'), 'value': x.get('value'), 'type': 'x'})
    if y:
        y_list = y if isinstance(y, list) else [y]

        for y_item in y_list:
            if isinstance(y_item, dict) and 'value' in y_item:
                y_obj = {
                    'name': y_item.get('name'),
                    'value': y_item.get('value'),
                    'type': 'y'
                }
                # 如果是multi-quota字段，添加标志
                if y_item.get('value') in multi_quota_fields:
                    y_obj['multi-quota'] = True
                axis.append(y_obj)
    if series:
        axis.append({'name': series.get('name'), 'value': series.get('value'), 'type': 'series'})
    if multi_quota_name:
        axis.append({'name': multi_quota_name, 'value': multi_quota_name, 'type': 'other-info'})

    request_obj = {
        "path": os.path.join(settings.MCP_IMAGE_PATH, file_name),
        "type": chart.get('type'),
        "data": orjson.dumps(data.get('data') if data.get('data') else []).decode(),
        "axis": orjson.dumps(axis).decode(),
    }

    _error = None
    try:
        requests.post(url=settings.MCP_IMAGE_HOST, json=request_obj, timeout=settings.SERVER_IMAGE_TIMEOUT)
    except Exception as e:
        _error = e

    request_path = urllib.parse.urljoin(settings.SERVER_IMAGE_HOST, f"{file_name}.png")

    return request_path, _error


def get_lang_name(lang: str):
    if not lang:
        return '简体中文'
    normalized = lang.lower()
    if normalized.startswith('zh-tw'):
        return '繁体中文'
    if normalized.startswith('en'):
        return '英文'
    if normalized.startswith('ko'):
        return '韩语'
    return '简体中文'
