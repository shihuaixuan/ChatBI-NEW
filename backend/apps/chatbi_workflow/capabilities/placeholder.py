from typing import Any


class PlaceholderChatBICapabilityGateway:
    """占位 ChatBI 能力网关，用于先跑通图执行闭环。"""

    def invoke(
        self,
        capability: str,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if capability == "query.understand":
            return {"normalized_question": request["question"]}
        if capability == "schema.retrieve":
            return {"tables": ["placeholder_table"], "fields": ["placeholder_metric"]}
        if capability == "sql.generate":
            return {"sql": "select 1 as placeholder_value"}
        if capability == "sql.validate":
            return {"valid": True, "reason": "placeholder_valid"}
        if capability == "permission.apply":
            return {"allowed": True, "reason": "placeholder_allowed"}
        if capability == "sql.execute":
            return {"rows": [{"placeholder_value": 1}]}
        if capability == "answer.generate":
            question = request["question"]
            return {
                "answer": f"这是图工作流占位回答：{question}",
                "warnings": ["当前为占位能力结果，未连接真实 ChatBI 能力"],
            }
        raise ValueError(f"UNKNOWN_PLACEHOLDER_CAPABILITY: {capability}")
