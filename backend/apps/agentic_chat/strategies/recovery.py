from apps.agentic_chat.schemas import AgenticDecision


class RecoveryPolicy:
    def decide(self, error_code: str) -> AgenticDecision:
        if error_code == "missing_slot":
            return AgenticDecision(action="ask_clarification", reason=error_code)
        if error_code == "low_confidence_evidence":
            return AgenticDecision(action="retrieve_evidence", reason=error_code)
        if error_code == "empty_result":
            return AgenticDecision(action="generate_answer", reason=error_code)
        return AgenticDecision(action="fail", reason=error_code)
