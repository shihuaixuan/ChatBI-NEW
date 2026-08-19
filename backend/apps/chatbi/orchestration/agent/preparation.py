"""Agent 首次执行与澄清恢复的输入准备流程。"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from typing import Any

from apps.chatbi.errors import QuestionUnderstandingError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.models.dto.question_understanding import QuestionRewriteOutput
from apps.chatbi.models.orm.agent_run import (
    AgentClarificationResumeKind,
    ChatbiAgentClarification,
)
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_results import ChatBIToolResultProcessor
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.understanding import (
    REWRITE_SYSTEM_PROMPT,
    SemanticParseService,
    StructuredModelService,
)
from apps.event import RenderEvent
from apps.retrieval import build_retrieval_request
from apps.tool import ToolStatus
from apps.tool.tools.semantic import SearchSemanticAssetsArgs, SearchSemanticAssetsTool


class AgentInputPreparer:
    """准备进入 Fast/Plan 模式所需的问题、候选资产和语义解析结果。"""

    def __init__(
        self,
        session: Any,
        rewrite_model_service: StructuredModelService,
        semantic_parse_service: SemanticParseService,
        search_tool: SearchSemanticAssetsTool,
        *,
        history_rounds: int = 3,
        node_logger: Callable[[str, Any], None] | None = None,
    ) -> None:
        if rewrite_model_service is None:
            raise ValueError("AGENT_REWRITE_MODEL_SERVICE_REQUIRED")
        if semantic_parse_service is None:
            raise ValueError("AGENT_SEMANTIC_PARSE_SERVICE_REQUIRED")
        if search_tool is None:
            raise ValueError("AGENT_SEARCH_TOOL_REQUIRED")
        if history_rounds < 0:
            raise ValueError("AGENT_HISTORY_ROUNDS_INVALID")
        self._session = session
        self._rewrite_model_service = rewrite_model_service
        self._semantic_parse_service = semantic_parse_service
        self._search_tool = search_tool
        self._history_rounds = history_rounds
        self._node_logger = node_logger
        self._result_processor = ChatBIToolResultProcessor()

    def prepare_initial(
        self,
        state: AgentRuntimeState,
    ) -> Generator[RenderEvent, None, bool]:
        """执行问题重写、候选检索和语义解析。"""

        original_question = str(state.record.question or "").strip()
        if not original_question:
            raise QuestionUnderstandingError("AGENT_INPUT_QUESTION_REQUIRED")
        if state.context.user_id is None or state.context.user_id <= 0:
            raise QuestionUnderstandingError("AGENT_INPUT_USER_REQUIRED")
        if state.context.dataset_id is None or state.context.dataset_id <= 0:
            raise QuestionUnderstandingError("AGENT_INPUT_DATASET_REQUIRED")

        temporal_context = state.temporal_context
        rewrite_input = {
            "current_question": original_question,
            "conversation_context": self._conversation_context(state),
            "reference_datetime": temporal_context.reference_at.isoformat(),
            "timezone": temporal_context.timezone,
        }
        rewrite_result = self._rewrite_model_service.invoke(
            QuestionModelInvocationData(
                stage="QUESTION_REWRITE",
                system_prompt=REWRITE_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    rewrite_input,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json_mode=QuestionModelJSONMode.STRICT,
            )
        )
        try:
            rewrite = QuestionRewriteOutput.model_validate(rewrite_result.payload)
        except ValueError as exc:
            raise QuestionUnderstandingError("QUESTION_REWRITE_OUTPUT_INVALID") from exc
        if rewrite.original_question != original_question:
            raise QuestionUnderstandingError(
                "QUESTION_REWRITE_ORIGINAL_QUESTION_MISMATCH"
            )

        state.budget.record_llm_usage(rewrite_result.usage_metadata)
        rewrite_payload = rewrite.model_dump(mode="json")
        state.context.state.update(
            {
                "original_question": original_question,
                "question": rewrite.rewrite_question,
                "question_rewrite": rewrite_payload,
            }
        )

        retrieval_request = build_retrieval_request(
            tenant_id=state.context.oid,
            actor_id=state.context.user_id,
            dataset_id=state.context.dataset_id,
            metric_phrases=list(rewrite.metric_phrases),
            dimension_phrases=list(rewrite.dimension_phrases),
            request_id=f"agent:{state.require_run_id()}:semantic-binding",
        )
        retrieval_payload = retrieval_request.model_dump(mode="json")
        state.context.state["semantic_retrieval_request"] = retrieval_payload
        self._log_node("节点输入 / 语义资产候选检索", retrieval_payload)

        retrieval_result = self._search_tool.execute(
            state.context,
            SearchSemanticAssetsArgs(),
        )
        if (
            retrieval_result.status is not ToolStatus.SUCCEEDED
            or retrieval_result.data is None
        ):
            raise QuestionUnderstandingError(
                retrieval_result.error_code or "SEMANTIC_CANDIDATE_RETRIEVAL_FAILED"
            )
        projection = self._result_processor.process(
            state.context,
            "search_semantic_assets",
            retrieval_result,
        )
        state.context.state.update(projection.state_patch)
        package = state.context.state.get("semantic_package")
        if not isinstance(package, dict):
            raise QuestionUnderstandingError("SEMANTIC_PACKAGE_REQUIRED")
        candidate_groups = normalize_candidate_groups(package.get("candidate_groups"))
        if not candidate_groups["metrics"]:
            raise QuestionUnderstandingError("SEMANTIC_METRIC_CANDIDATE_REQUIRED")
        state.context.state["candidate_groups"] = candidate_groups
        self._log_node(
            "节点输出 / 语义资产候选检索",
            {
                "candidate_groups": candidate_groups,
                "semantic_scope": state.context.state.get("semantic_scope"),
                "authorized_tables": state.context.state.get("allowed_tables"),
            },
        )

        semantic_parse = self._semantic_parse_service.parse(
            rewrite_question=rewrite.rewrite_question,
            candidate_payload={"candidate_groups": candidate_groups},
        )
        state.context.state["semantic_parse"] = semantic_parse.model_dump(mode="json")
        state.messages = [AgentMessage.user(rewrite.rewrite_question)]
        self._persist_snapshot(state)

        # RunOrchestrator 通过生成器协议调用输入准备器；这里没有额外业务事件。
        yield from ()
        return True

    def prepare_resume(
        self,
        state: AgentRuntimeState,
        clarification: ChatbiAgentClarification,
        answer_text: str,
    ) -> Generator[RenderEvent, None, bool]:
        """从语义解析边界恢复，只重做需要用户补充的语义解析。"""

        if (
            clarification.resume_kind
            != AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value
        ):
            raise QuestionUnderstandingError("AGENT_CLARIFICATION_RESUME_KIND_INVALID")
        if clarification.resume_payload.get("operation") != "resume_semantic_parse":
            raise QuestionUnderstandingError("AGENT_CLARIFICATION_OPERATION_INVALID")
        candidate_groups = state.context.state.get("candidate_groups")
        rewrite = state.context.state.get("question_rewrite")
        if not isinstance(candidate_groups, dict) or not isinstance(rewrite, dict):
            raise QuestionUnderstandingError("AGENT_CLARIFICATION_STATE_REQUIRED")
        rewrite_question = str(rewrite.get("rewrite_question") or "").strip()
        if not rewrite_question:
            raise QuestionUnderstandingError("AGENT_CLARIFICATION_QUESTION_REQUIRED")
        semantic_parse = self._semantic_parse_service.parse(
            rewrite_question=f"{rewrite_question}\n{answer_text}",
            candidate_payload={"candidate_groups": candidate_groups},
        )
        state.context.state["semantic_parse"] = semantic_parse.model_dump(mode="json")
        state.messages.append(AgentMessage.user(answer_text))
        self._persist_snapshot(state)
        yield from ()
        return True

    def _persist_snapshot(self, state: AgentRuntimeState) -> None:
        agent_run_repository.update_run(
            self._session,
            state.run,
            messages=state.serialized_messages(),
            budget_snapshot=state.budget_snapshot(),
            derived_state=state.persistable_context(),
        )
        self._session.commit()

    def _conversation_context(self, state: AgentRuntimeState) -> dict[str, Any]:
        """只提供问题重写所需的会话文本，不把旧问题理解快照传给新链路。"""

        history = agent_run_repository.recent_qa_summaries(
            self._session,
            state.run.chat_id,
            state.record.id,
            limit=self._history_rounds,
        )
        last_rewrite_question = agent_run_repository.latest_successful_rewrite_question(
            self._session,
            chat_id=state.run.chat_id,
            exclude_record_id=state.record.id,
            datasource_id=state.record.datasource,
        )
        context: dict[str, Any] = {}
        if history:
            context["history"] = history
        if last_rewrite_question:
            context["last_rewrite_question"] = last_rewrite_question
        return context

    def _log_node(self, title: str, payload: Any) -> None:
        """仅在手动测试传入日志回调时输出节点输入或输出。"""

        if self._node_logger is not None:
            self._node_logger(title, payload)


def normalize_candidate_groups(value: Any) -> dict[str, list[dict[str, Any]]]:
    """规范化候选资产，保证语义解析和模式路由使用同一份候选。"""

    if not isinstance(value, dict):
        raise QuestionUnderstandingError("SEMANTIC_CANDIDATE_GROUPS_REQUIRED")
    result: dict[str, list[dict[str, Any]]] = {"metrics": [], "dimensions": []}
    for group, asset_type in (("metrics", "METRIC"), ("dimensions", "DIMENSION")):
        items = value.get(group) or []
        if not isinstance(items, list):
            raise QuestionUnderstandingError("SEMANTIC_CANDIDATE_GROUPS_INVALID")
        for item in items:
            if not isinstance(item, dict):
                continue
            asset_id = item.get("asset_id")
            model_id = item.get("model_id")
            if (
                isinstance(asset_id, bool)
                or not isinstance(asset_id, int)
                or asset_id <= 0
                or isinstance(model_id, bool)
                or not isinstance(model_id, int)
                or model_id <= 0
            ):
                continue
            display_name = str(
                item.get("display_name")
                or item.get("biz_name")
                or item.get("name")
                or f"{asset_type}:{asset_id}"
            )
            result[group].append(
                {
                    **item,
                    "ref": str(
                        item.get("ref") or f"{asset_type}:{asset_id}:{model_id}"
                    ),
                    "asset_type": asset_type,
                    "asset_id": asset_id,
                    "model_id": model_id,
                    "display_name": display_name,
                    "biz_name": str(item.get("biz_name") or display_name),
                }
            )
    return result


__all__ = ["AgentInputPreparer", "normalize_candidate_groups"]
