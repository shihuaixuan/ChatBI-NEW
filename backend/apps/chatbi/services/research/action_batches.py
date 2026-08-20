"""Research 同轮动作的确定性批次划分。"""

from __future__ import annotations

from collections.abc import Iterable

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.research import (
    ResearchAction,
    ResearchDrilldownAction,
    ResearchFilterFromResultAction,
    ResearchValidateHypothesisAction,
)


def build_research_action_batches(
    actions: Iterable[ResearchAction],
    *,
    existing_result_ids: set[str],
    existing_evidence_ids: set[str],
    max_actions_per_batch: int,
) -> tuple[tuple[ResearchAction, ...], ...]:
    """只按已有结果引用划分批次，不替模型推断新的执行拓扑。"""

    if max_actions_per_batch <= 0:
        raise ValueError("RESEARCH_ACTION_BATCH_SIZE_INVALID")
    pending = tuple(actions)
    if len(pending) > max_actions_per_batch:
        raise ResearchExecutionError(
            ResearchExecutionError.BUDGET_EXHAUSTED,
            details={"reason": "RESEARCH_ACTIONS_PER_ITERATION_EXCEEDED"},
        )
    for action in pending:
        missing_refs = _missing_references(
            action,
            existing_result_ids=existing_result_ids,
            existing_evidence_ids=existing_evidence_ids,
        )
        if missing_refs:
            raise ResearchExecutionError(
                ResearchExecutionError.ACTION_BATCH_DEPENDENCY_INVALID,
                details={"missing_refs": sorted(missing_refs)},
            )
    if not pending:
        return ()
    # 当前动作契约只允许引用轮次开始前的结果，因此通过一次校验后即可并行。
    return (pending,)


def _missing_references(
    action: ResearchAction,
    *,
    existing_result_ids: set[str],
    existing_evidence_ids: set[str],
) -> set[str]:
    if isinstance(action, (ResearchFilterFromResultAction, ResearchDrilldownAction)):
        return {action.source_result_id} - existing_result_ids
    if isinstance(action, ResearchValidateHypothesisAction):
        return set(action.evidence_ids) - existing_evidence_ids
    return set()


__all__ = ["build_research_action_batches"]
