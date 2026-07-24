"""辅助生成编排装配：推荐问题、分析与预测。"""

from __future__ import annotations

from langchain.chat_models.base import BaseChatModel
from sqlmodel import Session

from apps.chatbi.adapters.analysis_prediction import (
    build_analysis_prediction_service,
)
from apps.chatbi.adapters.recommended_questions import (
    build_recommended_question_service,
)
from apps.chatbi.composition import (
    build_chat_log_service,
    build_chat_record_service,
    build_generation_context_service,
    build_generation_schema_context_service,
    build_history_query_service,
)
from apps.chatbi.services.generation.auxiliary_generation import (
    AuxiliaryGenerationService,
)


def build_auxiliary_generation_service(
    session: Session,
    llm: BaseChatModel,
) -> AuxiliaryGenerationService:
    """装配推荐问题、分析与预测共用的辅助生成编排。"""

    return AuxiliaryGenerationService(
        chat_record_service=build_chat_record_service(session),
        chat_log_service=build_chat_log_service(session),
        history_query_service=build_history_query_service(session),
        generation_context_service=build_generation_context_service(session),
        schema_context_service=build_generation_schema_context_service(session),
        analysis_prediction_service=build_analysis_prediction_service(
            session,
            llm,
        ),
        recommended_question_service=build_recommended_question_service(
            session,
            llm,
        ),
    )


__all__ = ["build_auxiliary_generation_service"]
