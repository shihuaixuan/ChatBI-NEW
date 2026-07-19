from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlmodel import Session

from apps.ai_model.streaming import process_stream
from apps.chatbi.chat_record import build_chat_record_service
from apps.chatbi.models import (
    SQLGenerationData,
    SQLGenerationMessage,
    SQLGenerationModelChunk,
)
from apps.chatbi.services import SQLGenerationService
from apps.template.generate_sql.generator import (
    get_sql_example_template,
    get_sql_template,
)


class TemplateSQLGenerationPromptBuilder:
    """使用现有 SQL 模板和历史消息生成稳定消息。"""

    def build(
        self,
        data: SQLGenerationData,
    ) -> list[SQLGenerationMessage]:
        sql_template_loader = cast(
            Callable[[str], dict[str, str]],
            get_sql_example_template,
        )
        base_template_loader = cast(
            Callable[[], dict[str, str]],
            get_sql_template,
        )
        sql_template = sql_template_loader(data.database_type)
        base_template = base_template_loader()
        process_check = sql_template.get("process_check") or base_template[
            "process_check"
        ]
        query_limit = (
            base_template["query_limit"]
            if data.enable_query_limit
            else base_template["no_query_limit"]
        )
        other_rule = sql_template["other_rule"].format(
            multi_table_condition=base_template["multi_table_condition"]
        )
        base_sql_rules = (
            sql_template["quot_rule"]
            + query_limit
            + sql_template["limit_rule"]
            + other_rule
        )
        messages = [
            SQLGenerationMessage(
                role="system",
                content=base_template["system"].format(
                    lang=data.language,
                    process_check=process_check,
                    sqlbot_name=data.assistant_name,
                ),
                system_context=True,
            ),
            SQLGenerationMessage(
                role="human",
                content=base_template["generate_rules"].format(
                    lang=data.language,
                    sqlbot_name=data.assistant_name,
                    base_sql_rules=base_sql_rules,
                    basic_sql_examples=sql_template["basic_example"],
                    example_engine=sql_template["example_engine"],
                    example_answer_1=(
                        sql_template["example_answer_1_with_limit"]
                        if data.enable_query_limit
                        else sql_template["example_answer_1"]
                    ),
                    example_answer_2=(
                        sql_template["example_answer_2_with_limit"]
                        if data.enable_query_limit
                        else sql_template["example_answer_2"]
                    ),
                    example_answer_3=(
                        sql_template["example_answer_3_with_limit"]
                        if data.enable_query_limit
                        else sql_template["example_answer_3"]
                    ),
                ),
                system_context=True,
            ),
            SQLGenerationMessage(
                role="ai",
                content=(
                    "我已掌握所有规则，包括表结构、SQL规范、安全限制和输出格式，"
                    "我会严格遵守这些规则。"
                ),
                system_context=True,
            ),
            SQLGenerationMessage(
                role="human",
                content=base_template["generate_basic_info"].format(
                    engine=data.engine,
                    schema=data.schema,
                    sample_data=data.sample_data,
                ),
                system_context=True,
            ),
            SQLGenerationMessage(
                role="ai",
                content=(
                    "我已确认您提供的数据库信息与表结构schema，"
                    "我生成的SQL不会超出您提供的范围。"
                ),
                system_context=True,
            ),
        ]
        optional_contexts = (
            (
                data.custom_prompt,
                "generate_custom_prompt_info",
                "custom_prompt",
                "我已确认您提供的额外信息，我会进行参考。",
            ),
            (
                data.terminologies,
                "generate_terminologies_info",
                "terminologies",
                "我已确认您提供的术语信息，我会进行参考。",
            ),
            (
                data.data_training,
                "generate_data_training_info",
                "data_training",
                "我已确认您提供的SQL示例，我会进行参考。",
            ),
        )
        for value, template_key, argument_name, confirmation in optional_contexts:
            if not value:
                continue
            messages.extend(
                [
                    SQLGenerationMessage(
                        role="human",
                        content=base_template[template_key].format(
                            **{argument_name: value}
                        ),
                        system_context=True,
                    ),
                    SQLGenerationMessage(
                        role="ai",
                        content=confirmation,
                        system_context=True,
                    ),
                ]
            )

        question = data.question
        if data.regenerate:
            question = base_template["regenerate_hint"] + question
        messages.extend(data.history)
        messages.append(
            SQLGenerationMessage(
                role="human",
                content=base_template["user"].format(
                    lang=data.language,
                    engine=data.engine,
                    schema=data.schema,
                    question=question,
                    rule=data.rule,
                    current_time=data.current_time,
                    error_msg=data.error_message,
                    change_title=data.change_title,
                ),
            )
        )
        return messages


class LangChainSQLGenerationModelClient:
    """把 SQL 消息 DTO 适配到现有 LangChain 流式模型。"""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm

    def stream(
        self,
        messages: list[SQLGenerationMessage],
    ) -> Iterator[SQLGenerationModelChunk]:
        langchain_messages: list[BaseMessage] = []
        for message in messages:
            if message.role == "system":
                langchain_messages.append(SystemMessage(content=message.content))
            elif message.role == "human":
                langchain_messages.append(HumanMessage(content=message.content))
            else:
                langchain_messages.append(AIMessage(content=message.content))

        token_usage: dict[str, int] = {}
        for chunk in process_stream(
            self._llm.stream(langchain_messages),
            token_usage,
        ):
            yield SQLGenerationModelChunk(
                content=chunk["content"],
                reasoning_content=chunk["reasoning_content"],
                token_usage=dict(token_usage),
            )


def build_sql_generation_service(
    session: Session,
    llm: BaseChatModel,
) -> SQLGenerationService:
    """装配主 SQL 生成所需的外层实现。"""

    return SQLGenerationService(
        prompt_builder=TemplateSQLGenerationPromptBuilder(),
        model_client=LangChainSQLGenerationModelClient(llm),
        chat_record_service=build_chat_record_service(session),
    )


__all__ = [
    "LangChainSQLGenerationModelClient",
    "TemplateSQLGenerationPromptBuilder",
    "build_sql_generation_service",
]
