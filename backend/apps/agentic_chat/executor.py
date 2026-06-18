from apps.agentic_chat.schemas import AgenticDecision, ToolResult
from apps.agentic_chat.state import AgenticState
from apps.agentic_chat.tool_registry import ToolRegistry


class AgenticExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute(self, decision: AgenticDecision, state: AgenticState) -> ToolResult:
        if decision.action == "understand_query":
            return self.registry.run(
                "query.understand",
                {
                    "question": state.question,
                    "chat_id": state.chat_id,
                    "record_id": state.record_id,
                    "run_id": state.run_id,
                    "oid": state.oid,
                    "user_id": state.user_id,
                    "datasource_id": state.datasource_id,
                    "confirmed_slots": state.confirmed_slots,
                    "history_slots": {},
                },
            )
        if decision.action == "retrieve_evidence":
            return self._retrieve_evidence(state)
        if decision.action == "route_strategy":
            return self._route_strategy(state)
        if decision.action == "generate_sql":
            allowed_tables = self._allowed_tables(state)
            return self.registry.run(
                decision.tool_name or "sql.generate_schema",
                {
                    "question": state.normalized_question or state.question,
                    "origin_question": state.question,
                    "allowed_tables": allowed_tables,
                    "slots": state.slots,
                    "datasource_id": state.datasource_id,
                    "oid": state.oid,
                    "user_id": state.user_id,
                },
            )
        if decision.action == "validate_sql":
            return self.registry.run(
                "sql.validate",
                {"sql": state.sql_candidate, "allowed_tables": self._allowed_tables(state)},
            )
        if decision.action == "apply_permission":
            return self.registry.run("permission.apply", {"sql": state.validated_sql, "datasource_id": state.datasource_id})
        if decision.action == "execute_sql":
            return self.registry.run("sql.execute", {"sql": state.permission_sql, "datasource_id": state.datasource_id})
        if decision.action == "generate_answer":
            return self.registry.run("answer.generate", {"execution_result_summary": state.execution_result_summary})
        return ToolResult(
            success=False,
            error_code="unknown_decision",
            message=f"Unknown agentic decision: {decision.action}",
        )

    def apply_result(self, decision: AgenticDecision, state: AgenticState, result: ToolResult) -> AgenticState:
        if decision.action == "understand_query":
            return state.apply_understanding(
                intent=result.payload.get("intent", "query_data"),
                slots=result.payload.get("slots") or {},
                missing_slots=result.payload.get("missing_slots") or [],
                normalized_question=result.payload.get("normalized_question"),
                intent_confidence=result.payload.get("intent_confidence"),
                low_confidence_slots=result.payload.get("low_confidence_slots") or [],
                ambiguous_slots=result.payload.get("ambiguous_slots") or [],
                conflict_slots=result.payload.get("conflict_slots") or [],
                retrieval_queries=result.payload.get("retrieval_queries") or [],
                confidence=result.payload.get("confidence"),
            )
        if decision.action == "retrieve_evidence":
            new_state = state
            for evidence_type, items in (result.payload.get("evidence") or {}).items():
                new_state = new_state.apply_evidence(evidence_type, items)
            return new_state
        if decision.action == "route_strategy":
            return state.apply_route(result.payload.get("strategy", "schema_text2sql"), result.payload.get("reason"))
        if decision.action == "generate_sql":
            return state.apply_sql_candidate(result.payload["sql"])
        if decision.action == "validate_sql":
            return state.apply_validated_sql(result.payload["sql"])
        if decision.action == "apply_permission":
            return state.apply_permission_sql(result.payload["sql"])
        if decision.action == "execute_sql":
            return state.apply_execution_result(result.payload)
        if decision.action == "generate_answer":
            return state.apply_answer(result.payload.get("answer", ""), result.payload.get("chart") or {})
        return state

    def _understand_query(self, state: AgenticState) -> ToolResult:
        missing_slots = []
        if not state.datasource_id:
            missing_slots.append("datasource")
        return ToolResult(
            success=True,
            payload={"intent": "query_data", "slots": {"question": state.question}, "missing_slots": missing_slots},
        )

    def _retrieve_evidence(self, state: AgenticState) -> ToolResult:
        schema_result = self.registry.run("schema.search", {"datasource_id": state.datasource_id, "question": state.question})
        if not schema_result.success:
            return schema_result
        semantic_result = self.registry.run("semantic.search", {"datasource_id": state.datasource_id, "question": state.question})
        term_result = self.registry.run("terminology.search", {"datasource_id": state.datasource_id, "question": state.question})
        example_result = self.registry.run("sql_example.search", {"datasource_id": state.datasource_id, "question": state.question})
        return ToolResult(
            success=True,
            payload={
                "evidence": {
                    "schema": schema_result.payload.get("items") or [],
                    "semantic": semantic_result.payload.get("items") or [],
                    "terminology": term_result.payload.get("items") or [],
                    "sql_example": example_result.payload.get("items") or [],
                }
            },
        )

    def _route_strategy(self, state: AgenticState) -> ToolResult:
        try:
            semantic_tool = self.registry.get("sql.generate_semantic_compiler")
        except KeyError:
            semantic_tool = None
        if semantic_tool is not None and hasattr(semantic_tool, "can_compile"):
            if semantic_tool.can_compile(oid=state.oid, datasource_id=state.datasource_id):
                return ToolResult(success=True, payload={"strategy": "semantic_sql_compiler", "reason": "headless_assets_available"})
        return ToolResult(success=True, payload={"strategy": "schema_text2sql", "reason": "default_schema_text2sql"})

    @staticmethod
    def _allowed_tables(state: AgenticState) -> list[str]:
        return [item.get("table") for item in state.evidence.get("schema", []) if item.get("table")]
