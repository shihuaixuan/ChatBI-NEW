import orjson

from apps.agentic_chat.schemas import AgenticEventPayload


def to_sse(payload: AgenticEventPayload | dict) -> str:
    data = payload.model_dump() if isinstance(payload, AgenticEventPayload) else payload
    return "data:" + orjson.dumps(data).decode() + "\n\n"


def sse_event(payload: AgenticEventPayload | dict) -> str:
    return to_sse(payload)


def _event(event_type: str, content: dict, record_id: int | None = None, run_id: int | None = None, step_index: int | None = None):
    return AgenticEventPayload(type=event_type, content=content, record_id=record_id, run_id=run_id, step_index=step_index)


def record_created(record_id: int, run_id: int) -> AgenticEventPayload:
    return _event("record-created", {"record_id": record_id, "id": record_id, "run_id": run_id}, record_id, run_id)


def run_started(record_id: int, run_id: int) -> AgenticEventPayload:
    return _event("run-started", {"record_id": record_id, "run_id": run_id}, record_id, run_id)


def step_started(record_id: int, run_id: int, step_index: int, action: str) -> AgenticEventPayload:
    return _event("step-started", {"record_id": record_id, "step_index": step_index, "action": action}, record_id, run_id, step_index)


def understanding(record_id: int, run_id: int, payload: dict) -> AgenticEventPayload:
    return _event("understanding", {"record_id": record_id, **payload}, record_id, run_id)


def understanding_summary(record_id: int, run_id: int, summary: dict) -> AgenticEventPayload:
    return _event("understanding-summary", {"record_id": record_id, "summary": summary}, record_id, run_id)


def tool_called(record_id: int, run_id: int, tool_name: str) -> AgenticEventPayload:
    return _event("tool-called", {"record_id": record_id, "tool_name": tool_name}, record_id, run_id)


def tool_result(record_id: int, run_id: int, payload: dict) -> AgenticEventPayload:
    return _event("tool-result", {"record_id": record_id, **payload}, record_id, run_id)


def route_selected(record_id: int, run_id: int, strategy: str, reason: str | None = None) -> AgenticEventPayload:
    return _event("route-selected", {"record_id": record_id, "strategy": strategy, "reason": reason}, record_id, run_id)


def clarification(
    record_id: int,
    run_id: int,
    clarification_id: int,
    target_slots: list[str],
    question: str,
    options: list[dict] | None = None,
) -> AgenticEventPayload:
    return _event(
        "clarification",
        {
            "record_id": record_id,
            "clarification_id": clarification_id,
            "target_slots": target_slots,
            "question": question,
            "options": options or [],
        },
        record_id,
        run_id,
    )


def clarification_accepted(record_id: int, run_id: int) -> AgenticEventPayload:
    return _event("clarification-accepted", {"record_id": record_id, "run_id": run_id}, record_id, run_id)


def sql_generated(record_id: int, run_id: int, sql: str) -> AgenticEventPayload:
    return _event("sql-generated", {"record_id": record_id, "sql": sql}, record_id, run_id)


def sql_validated(record_id: int, run_id: int, sql: str) -> AgenticEventPayload:
    return _event("sql-validated", {"record_id": record_id, "sql": sql}, record_id, run_id)


def sql_executed(record_id: int, run_id: int, row_count: int, fields: list[str]) -> AgenticEventPayload:
    return _event("sql-executed", {"record_id": record_id, "row_count": row_count, "fields": fields}, record_id, run_id)


def chart_generated(record_id: int, run_id: int, chart: dict) -> AgenticEventPayload:
    return _event("chart-generated", {"record_id": record_id, "chart": chart}, record_id, run_id)


def answer(record_id: int, run_id: int, content: str) -> AgenticEventPayload:
    return _event("answer", {"record_id": record_id, "content": content}, record_id, run_id)


def run_finished(record_id: int, run_id: int, content: str | None = None) -> AgenticEventPayload:
    return _event("run-finished", {"record_id": record_id, "content": content}, record_id, run_id)


def run_failed(record_id: int, run_id: int, content: str) -> AgenticEventPayload:
    return _event("run-failed", {"record_id": record_id, "content": content}, record_id, run_id)
