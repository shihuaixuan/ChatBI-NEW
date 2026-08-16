"""PLAN 模式的结构化规划提示词。"""

from __future__ import annotations

import json
from typing import Any

from apps.chatbi.models.dto.analysis_plan import AnalysisPlan

PLAN_SYSTEM_PROMPT = """
你是 ChatBI 的分析计划规划器。
只能使用输入中已经绑定的语义资产，不能发明 metric_id、dimension_id、表名或 SQL。
输出一个符合 AnalysisPlan JSON Schema 的对象；QueryTask 只描述语义查询，不能直接填写未经验证的 SQL。
需要跨结果集计算时使用白名单 ComputeTask：compare、growth、share、topn_other、pivot、expr。
如果输入资产不足以形成可验证计划，返回最小计划并在 validation.reason_codes 中说明原因。
""".strip()


def build_plan_prompt(
    *,
    question_understanding: dict[str, Any],
    semantic_state: dict[str, Any],
    verified_examples: list[dict[str, Any]] | None = None,
    instructions: list[str] | None = None,
) -> str:
    """构造不包含裸 SQL 的规划输入。"""

    payload = {
        "question_understanding": question_understanding,
        "semantic_state": {
            key: value
            for key, value in semantic_state.items()
            if key not in {"compiled_sql", "validated_sql", "full_data"}
        },
        "verified_examples": verified_examples or [],
        "instructions": instructions or [],
        "analysis_plan_schema": AnalysisPlan.model_json_schema(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["PLAN_SYSTEM_PROMPT", "build_plan_prompt"]
