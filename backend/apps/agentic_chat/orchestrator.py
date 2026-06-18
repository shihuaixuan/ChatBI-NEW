from collections.abc import Iterator

from apps.agentic_chat import crud
from apps.agentic_chat.events import sse_event
from apps.agentic_chat.executor import AgenticExecutor
from apps.agentic_chat.models import AgenticRun, AgenticRunStatus
from apps.agentic_chat.planner import RuleBasedPlanner
from apps.agentic_chat.schemas import AgenticConfig, AgenticEventPayload
from apps.agentic_chat.state import AgenticState
from apps.agentic_chat.tool_registry import ToolRegistry
from apps.agentic_chat.tools.answer_generator import AnswerGenerateTool
from apps.agentic_chat.tools.permission import PermissionTool
from apps.agentic_chat.tools.query_understanding import QueryUnderstandingTool
from apps.agentic_chat.tools.schema import SchemaTool
from apps.agentic_chat.tools.semantic_asset import SemanticAssetTool
from apps.agentic_chat.tools.semantic_sql_compiler import SemanticSQLCompilerTool
from apps.agentic_chat.tools.sql_example import SqlExampleTool
from apps.agentic_chat.tools.sql_executor import SqlExecuteTool
from apps.agentic_chat.tools.sql_generator import SqlGenerateTool
from apps.agentic_chat.tools.sql_validator import SqlValidateTool
from apps.agentic_chat.tools.terminology import TerminologyTool
from apps.chat.models.chat_model import ChatRecord
from common.core.deps import CurrentUser, SessionDep


class AgenticOrchestrator:
    def __init__(self, session: SessionDep, current_user: CurrentUser, config: AgenticConfig | None = None):
        self.session = session
        self.current_user = current_user
        self.config = config or AgenticConfig()
        self.planner = RuleBasedPlanner(max_steps=self.config.max_steps)
        self.executor = AgenticExecutor(self._build_registry())

    def run(
        self,
        run: AgenticRun,
        record: ChatRecord,
        initial_state: AgenticState | None = None,
        emit_initial: bool = True,
    ) -> Iterator[str]:
        state = initial_state or AgenticState(
            run_id=run.id,
            record_id=record.id,
            chat_id=record.chat_id,
            question=record.question or "",
            oid=run.oid,
            user_id=self.current_user.id,
            datasource_id=record.datasource,
        )
        current_step = None
        current_action = None
        try:
            run.status = AgenticRunStatus.RUNNING.value
            record.status = AgenticRunStatus.RUNNING.value
            self.session.add(run)
            self.session.add(record)
            self.session.commit()

            if emit_initial:
                yield self._emit(run.id, "record-created", {"record_id": record.id, "id": record.id, "run_id": run.id})
            yield self._emit(run.id, "run-started", {"record_id": record.id, "run_id": run.id})

            while True:
                decision = self.planner.next_action(state)
                current_action = decision.action
                if decision.action == "finish":
                    crud.update_run_state(self.session, run, AgenticRunStatus.FINISHED.value, "finish", state.model_dump())
                    crud.finish_record(self.session, record, AgenticRunStatus.FINISHED.value)
                    record.sql_answer = state.answer
                    record.sql = state.permission_sql or state.validated_sql or state.sql_candidate
                    if not record.data:
                        record.data = self._data_payload(state)
                    record.chart_answer = state.answer
                    record.chart = "{}"
                    self.session.add(record)
                    self.session.commit()
                    yield self._emit(run.id, "run-finished", {"record_id": record.id, "content": state.answer})
                    yield self._emit(run.id, "finish", {"record_id": record.id, "content": state.answer})
                    break
                if decision.action == "fail":
                    message = decision.reason or "agentic run failed"
                    crud.update_run_state(self.session, run, AgenticRunStatus.FAILED.value, "fail", state.model_dump())
                    crud.finish_record(self.session, record, AgenticRunStatus.FAILED.value, message)
                    run.error = message
                    self.session.add(run)
                    self.session.commit()
                    yield self._emit(run.id, "run-failed", {"record_id": record.id, "content": message})
                    yield self._emit(run.id, "error", {"record_id": record.id, "content": message})
                    break
                if decision.action == "ask_clarification":
                    target_slots = self._clarification_slots(state)
                    question, options = self._clarification_payload(state)
                    clarification = crud.create_clarification(
                        self.session,
                        run,
                        target_slots,
                        question,
                        self.current_user.id,
                        options=options,
                    )
                    crud.update_run_state(self.session, run, AgenticRunStatus.WAITING_USER.value, "ask_clarification", state.model_dump())
                    crud.finish_record(self.session, record, AgenticRunStatus.WAITING_USER.value)
                    self.session.commit()
                    yield self._emit(
                        run.id,
                        "clarification",
                        {
                            "record_id": record.id,
                            "clarification_id": clarification.id,
                            "target_slots": clarification.target_slots,
                            "question": clarification.question,
                            "options": clarification.options or [],
                        },
                    )
                    break

                step_index = state.step_count + 1
                current_step = crud.start_step(self.session, run, step_index, decision.action, decision.tool_name)
                self.session.commit()
                yield self._emit(run.id, "step-started", {"record_id": record.id, "step_index": step_index, "action": decision.action}, current_step.id)

                if decision.tool_name:
                    yield self._emit(run.id, "tool-called", {"record_id": record.id, "tool_name": decision.tool_name}, current_step.id)
                result = self.executor.execute(decision, state)
                if not result.success:
                    message = result.message or result.error_code or "tool failed"
                    state = state.apply_error(result.error_code or "tool_error", message).increase_step()
                    crud.fail_step(self.session, current_step, message)
                    crud.update_run_state(self.session, run, AgenticRunStatus.FAILED.value, decision.action, state.model_dump())
                    crud.finish_record(self.session, record, AgenticRunStatus.FAILED.value, message)
                    run.error = message
                    self.session.add(run)
                    self.session.commit()
                    yield self._emit(run.id, "run-failed", {"record_id": record.id, "content": message}, current_step.id)
                    yield self._emit(run.id, "error", {"record_id": record.id, "content": message}, current_step.id)
                    break

                state = self.executor.apply_result(decision, state, result).increase_step()
                if decision.action == "execute_sql":
                    # 完整结果仍写入 ChatRecord.data，AgenticState 只保留摘要和样例。
                    record.data = self._data_payload_from_result(result.payload)
                    self.session.add(record)
                crud.finish_step(self.session, current_step, self._public_summary(decision.action, result.payload))
                crud.update_run_state(self.session, run, AgenticRunStatus.RUNNING.value, decision.action, state.model_dump())
                self.session.commit()
                yield self._emit(
                    run.id,
                    "step-finished",
                    {
                        "record_id": record.id,
                        "step_index": step_index,
                        "action": decision.action,
                        "summary": self._public_summary(decision.action, result.payload),
                    },
                    current_step.id,
                )
                if decision.tool_name:
                    yield self._emit(run.id, "tool-result", {"record_id": record.id, **self._public_summary(decision.action, result.payload)}, current_step.id)
                for event_type, payload in self._public_events_for_action(decision.action, record.id, run.id, result.payload):
                    yield self._emit(run.id, event_type, payload, current_step.id)
                current_step = None
        except Exception as exc:
            # 任意未预期异常都收敛成可观察的失败事件，避免 SSE 静默中断。
            message = str(exc) or exc.__class__.__name__
            state = state.apply_error("unexpected_error", message)
            if current_step is not None:
                crud.fail_step(self.session, current_step, message)
            crud.update_run_state(self.session, run, AgenticRunStatus.FAILED.value, current_action or "unexpected_error", state.model_dump())
            crud.finish_record(self.session, record, AgenticRunStatus.FAILED.value, message)
            run.error = message
            self.session.add(run)
            self.session.commit()
            yield self._emit(run.id, "run-failed", {"record_id": record.id, "content": message}, getattr(current_step, "id", None))
            yield self._emit(run.id, "error", {"record_id": record.id, "content": message}, getattr(current_step, "id", None))

    def resume(self, run: AgenticRun, record: ChatRecord, answers: dict) -> Iterator[str]:
        # 恢复时从快照重建状态，只合并用户确认槽位，再继续执行 Planner-Executor 循环。
        state = AgenticState(**run.state).apply_clarification(answers)
        run.state = state.model_dump()
        run.status = AgenticRunStatus.RUNNING.value
        record.status = AgenticRunStatus.RUNNING.value
        self.session.add(run)
        self.session.add(record)
        self.session.commit()
        yield self._emit(run.id, "clarification-accepted", {"record_id": record.id, "run_id": run.id})
        yield from self.run(run, record, initial_state=state, emit_initial=False)

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(QueryUnderstandingTool(session=self.session))
        registry.register(SchemaTool(self.session))
        registry.register(SemanticAssetTool())
        registry.register(TerminologyTool())
        registry.register(SqlExampleTool())
        registry.register(SemanticSQLCompilerTool(self.session))
        registry.register(SqlGenerateTool())
        registry.register(SqlValidateTool(default_limit=self.config.default_limit))
        registry.register(PermissionTool())
        registry.register(SqlExecuteTool(self.session))
        registry.register(AnswerGenerateTool())
        return registry

    def _emit(self, run_id: int, event_type: str, payload: dict, step_id: int | None = None) -> str:
        crud.append_trace(self.session, run_id, event_type, payload, step_id=step_id)
        self.session.commit()
        return sse_event(AgenticEventPayload(type=event_type, content=payload, record_id=payload.get("record_id"), run_id=run_id))

    @staticmethod
    def _clarification_question(slots: list[str]) -> str:
        if "datasource" in slots:
            return "请先选择本次问数使用的数据源。"
        return "请补充问题中的关键信息：" + "、".join(slots)

    @staticmethod
    def _clarification_slots(state: AgenticState) -> list[str]:
        slots = list(state.missing_slots)
        for issue in [*state.low_confidence_slots, *state.ambiguous_slots, *state.conflict_slots]:
            slot = issue.get("slot")
            if slot and slot not in slots:
                slots.append(slot)
        return slots

    @classmethod
    def _clarification_payload(cls, state: AgenticState) -> tuple[str, list[dict]]:
        if state.ambiguous_slots:
            issue = state.ambiguous_slots[0]
            if issue.get("slot") == "metrics":
                options = cls._metric_clarification_options(issue)
                return "你想查询哪种额度？" if issue.get("raw_text") == "额度" else "你想查询哪个指标？", options
            return issue.get("reason") or "请确认你的查询口径。", []
        if state.low_confidence_slots:
            issue = state.low_confidence_slots[0]
            if issue.get("slot") == "metrics":
                options = cls._metric_clarification_options(issue, include_other=bool(issue.get("candidates")))
                if options:
                    return "我不确定你要查询哪个指标，请确认。", options
            return issue.get("reason") or "请确认问题中的关键信息。", []
        if state.conflict_slots:
            issue = state.conflict_slots[0]
            return issue.get("reason") or "当前问题与已确认信息存在冲突，请确认。", []
        return cls._clarification_question(state.missing_slots), []

    @staticmethod
    def _metric_clarification_options(issue: dict, include_other: bool = True) -> list[dict]:
        options = [
            {
                "slot": "metrics",
                "label": candidate.get("display_name"),
                "value": candidate.get("display_name"),
                "asset_type": candidate.get("asset_type"),
                "asset_id": candidate.get("asset_id"),
            }
            for candidate in issue.get("candidates", [])
            if candidate.get("display_name")
        ]
        if include_other:
            options.append({"slot": "metrics", "label": "其他，请补充", "value": "__other__"})
        return options

    @staticmethod
    def _public_summary(action: str, payload: dict) -> dict:
        if action == "execute_sql":
            data = payload.get("data") or []
            return {"row_count": len(data), "fields": payload.get("fields") or []}
        if "sql" in payload:
            return {"sql": payload["sql"]}
        return {key: value for key, value in payload.items() if key not in {"data"}}

    @staticmethod
    def _public_events_for_action(action: str, record_id: int, run_id: int, payload: dict) -> list[tuple[str, dict]]:
        # 将内部 step 结果转为前端稳定事件名，保留 step-finished 作为通用事件。
        if action == "understand_query":
            return [("understanding", {"record_id": record_id, **payload})]
        if action == "route_strategy":
            return [
                (
                    "route-selected",
                    {
                        "record_id": record_id,
                        "strategy": payload.get("strategy"),
                        "reason": payload.get("reason"),
                    },
                )
            ]
        if action == "generate_sql":
            return [("sql-generated", {"record_id": record_id, "sql": payload.get("sql")})]
        if action == "validate_sql":
            return [("sql-validated", {"record_id": record_id, "sql": payload.get("sql")})]
        if action == "execute_sql":
            data = payload.get("data") or []
            return [
                (
                    "sql-executed",
                    {
                        "record_id": record_id,
                        "row_count": len(data),
                        "fields": payload.get("fields") or [],
                    },
                )
            ]
        if action == "generate_answer":
            events = [("answer", {"record_id": record_id, "content": payload.get("answer")})]
            if payload.get("chart") is not None:
                events.append(("chart-generated", {"record_id": record_id, "chart": payload.get("chart")}))
            return events
        return []

    @staticmethod
    def _data_payload(state: AgenticState) -> str:
        import orjson

        return orjson.dumps({"fields": state.execution_result_summary.get("fields", []) if state.execution_result_summary else [], "data": state.execution_sample}).decode()

    @staticmethod
    def _data_payload_from_result(result: dict) -> str:
        import orjson

        return orjson.dumps({"fields": result.get("fields") or [], "data": result.get("data") or []}).decode()
