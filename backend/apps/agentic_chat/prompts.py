from apps.agentic_chat.services.query_understanding_prompt import (
    build_query_understanding_prompts,
)

QUERY_UNDERSTANDING_PROMPT = """你是 Numora 的问数理解模块，请识别用户问题中的指标、维度、时间和过滤条件。"""

__all__ = ["QUERY_UNDERSTANDING_PROMPT", "build_query_understanding_prompts"]
