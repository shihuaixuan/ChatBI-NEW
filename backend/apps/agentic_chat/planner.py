from apps.agentic_chat.schemas import AgenticDecision
from apps.agentic_chat.state import AgenticState


class RuleBasedPlanner:
    def __init__(self, max_steps: int = 12):
        self.max_steps = max_steps

    def next_action(self, state: AgenticState) -> AgenticDecision:
        if state.step_count > self.max_steps:
            return AgenticDecision(action="fail", reason="step_budget_exceeded")
        if not state.intent:
            return AgenticDecision(action="understand_query", tool_name="query.understand")
        if state.missing_slots:
            return AgenticDecision(action="ask_clarification", reason="missing_slots")
        if state.low_confidence_slots:
            return AgenticDecision(action="ask_clarification", reason="low_confidence_slots")
        if state.ambiguous_slots:
            return AgenticDecision(action="ask_clarification", reason="ambiguous_slots")
        if state.conflict_slots:
            return AgenticDecision(action="ask_clarification", reason="conflict_slots")
        if not state.evidence:
            return AgenticDecision(action="retrieve_evidence", tool_name="evidence.retrieve")
        if not state.strategy:
            return AgenticDecision(action="route_strategy", reason="default_schema_text2sql")
        if not state.sql_candidate:
            return AgenticDecision(action="generate_sql", tool_name=self._sql_generation_tool(state.strategy))
        if not state.validated_sql:
            return AgenticDecision(action="validate_sql", tool_name="sql.validate")
        if not state.permission_sql:
            return AgenticDecision(action="apply_permission", tool_name="permission.apply")
        if not state.execution_result_summary:
            return AgenticDecision(action="execute_sql", tool_name="sql.execute")
        if not state.answer:
            return AgenticDecision(action="generate_answer", tool_name="answer.generate")
        return AgenticDecision(action="finish")

    @staticmethod
    def _sql_generation_tool(strategy: str) -> str:
        if strategy == "semantic_sql_compiler":
            return "sql.generate_semantic_compiler"
        return "sql.generate_schema"
