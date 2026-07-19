from datetime import datetime
from typing import Any, List, Optional, Union

from fastapi import Body
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from apps.chatbi.models import (
    Chat as Chat,
)
from apps.chatbi.models import (
    ChatFinishStep as ChatFinishStep,
)
from apps.chatbi.models import (
    ChatInfo as ChatInfo,
)
from apps.chatbi.models import (
    ChatLog as ChatLog,
)
from apps.chatbi.models import (
    ChatRecord as ChatRecord,
)
from apps.chatbi.models import (
    CreateChat as CreateChat,
)
from apps.chatbi.models import (
    OperationEnum as OperationEnum,
)
from apps.chatbi.models import (
    QuickCommand as QuickCommand,
)
from apps.chatbi.models import (
    RenameChat as RenameChat,
)
from apps.chatbi.models import (
    TypeEnum as TypeEnum,
)


class ChatRecordResult(BaseModel):
    id: Optional[int] = None
    chat_id: Optional[int] = None
    ai_modal_id: Optional[int] = None
    first_chat: bool = False
    create_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None
    question: Optional[str] = None
    sql_answer: Optional[str] = None
    sql: Optional[str] = None
    dataset_id: Optional[int] = None
    datasource: Optional[int] = None
    data: Optional[str] = None
    chart_answer: Optional[str] = None
    chart: Optional[str] = None
    analysis: Optional[str] = None
    predict: Optional[str] = None
    predict_data: Optional[str] = None
    recommended_question: Optional[str] = None
    datasource_select_answer: Optional[str] = None
    finish: Optional[bool] = None
    status: str | None = None
    trace_id: str | None = None
    # 响应层保持可选，兼容尚未投影执行类型的历史查询结果。
    execution_type: str | None = None
    error: Optional[str] = None
    analysis_record_id: Optional[int] = None
    predict_record_id: Optional[int] = None
    regenerate_record_id: Optional[int] = None
    sql_reasoning_content: Optional[str] = None
    chart_reasoning_content: Optional[str] = None
    analysis_reasoning_content: Optional[str] = None
    predict_reasoning_content: Optional[str] = None
    duration: Optional[float] = None  # 耗时字段（单位：秒）
    total_tokens: Optional[int] = None  # token总消耗


class ChatLogHistoryItem(BaseModel):
    start_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None
    duration: Optional[float] = None  # 耗时字段（单位：秒）
    total_tokens: Optional[int] = None  # token总消耗
    operate: Optional[str] = None
    local_operation: Optional[bool] = False
    message: Optional[str | dict | list] = None
    error: Optional[bool] = False


class ChatLogHistory(BaseModel):
    start_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None
    duration: Optional[float] = None  # 耗时字段（单位：秒）
    total_tokens: Optional[int] = None  # token总消耗
    steps: List[ChatLogHistoryItem | dict] = []


class AiModelQuestion(BaseModel):
    question: str = None
    ai_modal_id: int = None
    ai_modal_name: str = None  # Specific model name
    engine: str = ""
    db_schema: str = ""
    sql: str = ""
    rule: str = ""
    fields: str = ""
    data: str = ""
    lang: str = "简体中文"
    filter: str = []
    sub_query: Optional[list[dict]] = None
    terminologies: str = ""
    data_training: str = ""
    semantic_context: str = ""
    custom_prompt: str = ""
    error_msg: str = ""
    regenerate_record_id: Optional[int] = None
    sample_data: str = ""
    sqlbot_name: str = "Numora"

class ChatQuestion(AiModelQuestion):
    chat_id: int
    datasource_id: Optional[int] = None


class ChatMcp(ChatQuestion):
    token: str


class McpDs(BaseModel):
    token: str = Body(description='用户token')
    oid: Optional[str] = Body(description='组织ID，如果不传则为最后一次登录 Numora 时所使用的组织ID', default=None)


class ChatStart(BaseModel):
    username: str = Body(description='用户名')
    password: str = Body(description='密码')


class McpQuestion(BaseModel):
    question: str = Body(description='用户提问')
    chat_id: int = Body(description='会话ID')
    token: str = Body(description='token')
    stream: Optional[bool] = Body(description='是否流式输出，默认为true开启, 关闭false则返回JSON对象', default=True)
    lang: Optional[str] = Body(description='语言：zh-CN|zh-TW|en|ko-KR', default='zh-CN')
    datasource_id: Optional[int | str] = Body(description='数据源ID，仅当当前对话没有确定数据源时有效', default=None)
    oid: Optional[str] = Body(
        description='组织ID，仅当数据源ID为空时有效，如果不传则为最后一次登录 Numora 时所使用的组织ID', default=None)
    return_img: Optional[bool] = Body(description='是否返回图表，默认为true开启, 关闭false则仅返回数据', default=True)


class AxisObj(BaseModel):
    name: str = ''
    value: str = ''
    type: str | None = None


class ExcelData(BaseModel):
    axis: list[AxisObj] = []
    data: list[dict] = []
    name: str = 'Excel'


class SystemPromptMessage(SystemMessage):
    sqlbot_system: bool = True

    def __init__(
            self, content: Union[str, list[Union[str, dict]]], **kwargs: Any
    ) -> None:
        super().__init__(content=content, **kwargs)


class HumanPromptMessage(HumanMessage):
    sqlbot_system: bool = True

    def __init__(
            self, content: Union[str, list[Union[str, dict]]], **kwargs: Any
    ) -> None:
        super().__init__(content=content, **kwargs)


class AIPromptMessage(AIMessage):
    sqlbot_system: bool = True

    def __init__(
            self, content: Union[str, list[Union[str, dict]]], **kwargs: Any
    ) -> None:
        super().__init__(content=content, **kwargs)
