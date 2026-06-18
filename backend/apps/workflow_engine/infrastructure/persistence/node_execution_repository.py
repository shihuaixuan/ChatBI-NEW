from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, func, select

from apps.workflow_engine.domain.definition import NodeDefinition
from apps.workflow_engine.domain.execution import NodeExecutionResult
from apps.workflow_engine.domain.run import WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import NodeExecutionModel
from apps.workflow_engine.runtime.router import RouteDecision


class NodeExecutionRepository:
    """把节点执行尝试记录到 node_execution 表。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        run: WorkflowRun,
        node: NodeDefinition,
        result: NodeExecutionResult,
        route: RouteDecision | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        sequence = self._next_sequence(run.run_id)
        attempt = self._next_attempt(run.run_id, node.name, run.context.control.loop_iterations.get(node.name, 1))
        self._session.add(
            NodeExecutionModel(
                run_id=run.run_id,
                sequence=sequence,
                node_name=node.name,
                node_type=node.type.value,
                handler=node.handler,
                attempt=attempt,
                idempotency_key=f"{run.run_id}:{node.name}:{attempt}",
                status=result.status.value,
                input_summary=self._input_summary(run),
                output_summary=self._output_summary(result),
                route_summary=self._route_summary(route),
                error_code=result.error.code if result.error is not None else None,
                error_message=result.error.message if result.error is not None else None,
                created_at=now,
                finished_at=now,
            )
        )
        self._session.flush()

    def _next_sequence(self, run_id: str) -> int:
        latest_sequence = self._session.exec(
            select(func.max(col(NodeExecutionModel.sequence))).where(NodeExecutionModel.run_id == run_id)
        ).one()
        return int(latest_sequence or 0) + 1

    def _next_attempt(self, run_id: str, node_name: str, visit_count: int) -> int:
        latest_attempt = self._session.exec(
            select(func.max(col(NodeExecutionModel.attempt))).where(
                NodeExecutionModel.run_id == run_id,
                NodeExecutionModel.node_name == node_name,
            )
        ).one()
        return max(int(latest_attempt or 0) + 1, visit_count)

    def _input_summary(self, run: WorkflowRun) -> dict[str, Any]:
        request = run.context.request
        return {
            key: request[key]
            for key in ("question", "datasource_id")
            if key in request
        }

    def _output_summary(self, result: NodeExecutionResult) -> dict[str, Any]:
        set_values = result.patch.set_values
        if not set_values:
            return {}
        if len(set_values) == 1:
            value = next(iter(set_values.values()))
            return value if isinstance(value, dict) else {"value": value}
        return dict(set_values)

    def _route_summary(self, route: RouteDecision | None) -> dict[str, Any]:
        if route is None:
            return {}
        return {
            "target": route.target,
            "condition": route.condition,
            "reason_code": route.reason_code,
            "reason_summary": route.reason_summary,
        }
