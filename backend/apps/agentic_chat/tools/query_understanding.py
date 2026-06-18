from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from apps.agentic_chat.schemas import ToolResult
from apps.agentic_chat.services.query_understanding import QueryUnderstandingService
from apps.agentic_chat.services.query_understanding_candidates import (
    QueryUnderstandingCandidateBuilder,
)
from apps.agentic_chat.services.query_understanding_models import (
    QueryUnderstandingContext,
    SlotCandidate,
)
from apps.agentic_chat.services.query_understanding_prompt import (
    build_query_understanding_prompts,
)
from apps.ai_model.model_factory import LLMFactory, get_default_config


class DefaultQueryUnderstandingModelClient:
    def __init__(self):
        self._llm = None

    def __call__(self, context: QueryUnderstandingContext) -> str:
        llm = self._get_llm()
        system_prompt, user_prompt = self._build_prompts(context)
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        return str(getattr(response, "content", response) or "")

    def _get_llm(self):
        if self._llm is None:
            # 默认模型配置读取依赖异步解密逻辑；这里在工具线程中同步等待即可。
            config = asyncio.run(get_default_config())
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm

    @staticmethod
    def _build_prompts(context: QueryUnderstandingContext) -> tuple[str, str]:
        metric_candidates = [
            candidate.model_dump()
            for candidate in context.semantic_candidates
            if DefaultQueryUnderstandingModelClient._candidate_type(candidate) == "METRIC"
        ]
        dimension_candidates = [
            candidate.model_dump()
            for candidate in context.semantic_candidates
            if DefaultQueryUnderstandingModelClient._candidate_type(candidate) == "DIMENSION"
        ]
        datasource_summary: dict[str, Any] = {"datasource_id": context.datasource_id}
        return build_query_understanding_prompts(
            current_date=context.current_time.strftime("%Y-%m-%d"),
            question=context.question,
            confirmed_slots=context.confirmed_slots,
            datasource_summary=datasource_summary,
            metric_candidates=metric_candidates,
            dimension_candidates=dimension_candidates,
            terminology_candidates=context.terminology_candidates,
            schema_summary=context.schema_summary,
        )

    @staticmethod
    def _candidate_type(candidate: SlotCandidate) -> str:
        return str(candidate.asset_type or "").upper()


def build_default_query_understanding_model_client() -> DefaultQueryUnderstandingModelClient:
    return DefaultQueryUnderstandingModelClient()


class QueryUnderstandingTool:
    name = "query.understand"

    def __init__(self, service: QueryUnderstandingService | None = None, session=None):
        self.service = service or QueryUnderstandingService(model_client=build_default_query_understanding_model_client())
        self.candidate_builder = QueryUnderstandingCandidateBuilder(session)

    def run(self, payload: dict) -> ToolResult:
        try:
            datasource_id = payload.get("datasource_id")
            oid = payload.get("oid") or 1
            user_id = payload.get("user_id")
            semantic_candidates = payload.get("semantic_candidates")
            schema_summary = payload.get("schema_summary")
            terminology_candidates = payload.get("terminology_candidates")
            if semantic_candidates is None:
                semantic_candidates = self.candidate_builder.build_semantic_candidates(datasource_id, oid=oid, user_id=user_id)
            if schema_summary is None:
                schema_summary = self.candidate_builder.build_schema_summary(datasource_id)
            if terminology_candidates is None:
                terminology_candidates = self.candidate_builder.build_terminology_candidates(datasource_id, payload.get("question") or "")
            context = QueryUnderstandingContext(
                question=payload.get("question") or "",
                chat_id=payload.get("chat_id") or 0,
                record_id=payload.get("record_id") or 0,
                oid=oid,
                user_id=user_id,
                datasource_id=datasource_id,
                current_time=payload.get("current_time") or datetime.now(),
                confirmed_slots=payload.get("confirmed_slots") or {},
                history_slots=payload.get("history_slots") or {},
                semantic_candidates=semantic_candidates or [],
                terminology_candidates=terminology_candidates or [],
                schema_summary=schema_summary or [],
            )
            result = self.service.understand(context)
            return ToolResult(success=True, payload=result.dump_for_tool())
        except Exception:
            # 工具错误只暴露稳定错误码，避免将内部堆栈或敏感上下文发给前端。
            return ToolResult(success=False, error_code="query_understanding_error", message="问题理解失败")
