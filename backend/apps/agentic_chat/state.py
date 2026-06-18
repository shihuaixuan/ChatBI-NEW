from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field


class AgenticSlot(BaseModel):
    name: str
    value: Any = None
    display_name: str | None = None
    confidence: float = 0.0
    source: str = "unknown"
    asset_type: str | None = None
    asset_id: int | None = None
    confirmed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgenticState(BaseModel):
    run_id: int
    record_id: int
    chat_id: int
    question: str
    oid: int = 1
    user_id: int | None = None
    datasource_id: int | None = None
    normalized_question: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    confirmed_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    low_confidence_slots: list[dict[str, Any]] = Field(default_factory=list)
    ambiguous_slots: list[dict[str, Any]] = Field(default_factory=list)
    conflict_slots: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    understanding_confidence: float | None = None
    evidence: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    strategy: str | None = None
    strategy_history: list[dict[str, Any]] = Field(default_factory=list)
    sql_candidate: str | None = None
    validated_sql: str | None = None
    permission_sql: str | None = None
    execution_result_summary: dict[str, Any] | None = None
    execution_sample: list[dict[str, Any]] = Field(default_factory=list)
    answer: str | None = None
    chart: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    step_count: int = 0
    tool_call_count: int = 0
    budgets: dict[str, int] = Field(default_factory=lambda: {"used_steps": 0, "used_tool_calls": 0})

    def _copy(self, **updates: Any) -> AgenticState:
        data = self.model_dump()
        data.update(updates)
        return AgenticState(**data)

    def apply_understanding(
        self,
        intent: str,
        slots: dict[str, Any],
        missing_slots: list[str],
        normalized_question: str | None = None,
        intent_confidence: float | None = None,
        low_confidence_slots: list[dict[str, Any]] | None = None,
        ambiguous_slots: list[dict[str, Any]] | None = None,
        conflict_slots: list[dict[str, Any]] | None = None,
        retrieval_queries: list[str] | None = None,
        confidence: float | None = None,
    ) -> AgenticState:
        # 已确认槽位来自用户澄清，后续理解结果只能补充，不能覆盖。
        confirmed = deepcopy(self.confirmed_slots)
        for key, value in slots.items():
            confirmed.setdefault(key, value)
        merged_slots = deepcopy(self.slots)
        merged_slots.update(slots)
        merged_slots.update(confirmed)
        return self._copy(
            intent=intent,
            normalized_question=normalized_question or self.normalized_question,
            intent_confidence=intent_confidence,
            slots=merged_slots,
            confirmed_slots=confirmed,
            missing_slots=missing_slots,
            low_confidence_slots=low_confidence_slots or [],
            ambiguous_slots=ambiguous_slots or [],
            conflict_slots=conflict_slots or [],
            retrieval_queries=retrieval_queries or [],
            understanding_confidence=confidence,
        )

    def apply_clarification(self, answers: dict[str, Any]) -> AgenticState:
        confirmed = deepcopy(self.confirmed_slots)
        confirmed.update(answers)
        slots = deepcopy(self.slots)
        slots.update(answers)
        answered_slots = set(answers)
        missing = [slot for slot in self.missing_slots if slot not in answered_slots]
        low_confidence = [issue for issue in self.low_confidence_slots if issue.get("slot") not in answered_slots]
        ambiguous = [issue for issue in self.ambiguous_slots if issue.get("slot") not in answered_slots]
        conflict = [issue for issue in self.conflict_slots if issue.get("slot") not in answered_slots]
        return self._copy(
            confirmed_slots=confirmed,
            slots=slots,
            missing_slots=missing,
            low_confidence_slots=low_confidence,
            ambiguous_slots=ambiguous,
            conflict_slots=conflict,
        )

    def apply_evidence(self, evidence_type: str, items: list[dict[str, Any]]) -> AgenticState:
        evidence = deepcopy(self.evidence)
        evidence[evidence_type] = items
        return self._copy(evidence=evidence)

    def apply_tool_result(self, tool_name: str, result) -> AgenticState:
        if not result.success:
            return self.apply_error(result.error_code or "tool_error", result.message or "tool failed")
        payload = result.payload
        if tool_name in {"schema.search", "semantic.search", "terminology.search", "sql_example.search"}:
            evidence_type = {
                "schema.search": "schema",
                "semantic.search": "semantic_assets",
                "terminology.search": "terms",
                "sql_example.search": "sql_examples",
            }[tool_name]
            return self.apply_evidence(evidence_type, payload.get("items") or [])
        if tool_name in {"sql.generate_schema", "sql.generate_semantic", "template.fill"} and payload.get("sql"):
            return self.apply_sql_candidate(payload["sql"])
        if tool_name == "sql.validate" and payload.get("sql"):
            return self.apply_validated_sql(payload["sql"])
        if tool_name == "permission.apply" and payload.get("sql"):
            return self.apply_permission_sql(payload["sql"])
        if tool_name == "sql.execute":
            return self.apply_execution_result(payload)
        if tool_name == "answer.generate":
            return self.apply_answer(payload.get("answer", ""), payload.get("chart") or {})
        return self

    def apply_route(self, strategy: str, reason: str | None = None) -> AgenticState:
        history = deepcopy(self.strategy_history)
        history.append({"strategy": strategy, "reason": reason})
        return self._copy(strategy=strategy, strategy_history=history)

    def apply_sql_candidate(self, sql: str) -> AgenticState:
        return self._copy(sql_candidate=sql)

    def apply_validated_sql(self, sql: str) -> AgenticState:
        return self._copy(validated_sql=sql)

    def apply_permission_sql(self, sql: str) -> AgenticState:
        return self._copy(permission_sql=sql)

    def apply_execution_result(self, result: dict[str, Any]) -> AgenticState:
        data = result.get("data") or []
        fields = result.get("fields") or []
        # 状态只保存摘要和少量样例，避免把完整结果集塞进 agentic_run.state_snapshot。
        summary = {"row_count": len(data), "fields": fields}
        return self._copy(execution_result_summary=summary, execution_sample=data[:5])

    def apply_answer(self, answer: str, chart: dict[str, Any] | None = None) -> AgenticState:
        return self._copy(answer=answer, chart=chart or {})

    def apply_error(self, error_code: str, message: str) -> AgenticState:
        errors = deepcopy(self.errors)
        errors.append({"error_code": error_code, "message": message})
        return self._copy(errors=errors)

    def increase_step(self) -> AgenticState:
        budgets = deepcopy(self.budgets)
        budgets["used_steps"] = budgets.get("used_steps", self.step_count) + 1
        return self._copy(step_count=self.step_count + 1, budgets=budgets)

    def increase_tool_call(self) -> AgenticState:
        budgets = deepcopy(self.budgets)
        budgets["used_tool_calls"] = budgets.get("used_tool_calls", self.tool_call_count) + 1
        return self._copy(tool_call_count=self.tool_call_count + 1, budgets=budgets)
