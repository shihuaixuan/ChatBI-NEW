from typing import Any

from apps.chatbi_workflow.capabilities.adapters.interaction import InteractionAdapter
from apps.chatbi_workflow.capabilities.execution import validate_execution_output


class PlaceholderChatBICapabilityGateway:
    """占位 ChatBI 能力网关，用于先跑通图执行闭环。"""

    def __init__(self) -> None:
        self._interaction_adapter = InteractionAdapter()

    def invoke(
        self,
        capability: str,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if capability == "query.understand":
            return {"normalized_question": request["question"]}
        if capability == "schema.retrieve":
            return {"tables": ["placeholder_table"], "fields": ["placeholder_metric"]}
        if capability == "plan.bind":
            knowledge = request.get("variables", {}).get("knowledge", {})
            if not knowledge.get("hit"):
                return {"status": "infeasible", "infeasible_reason": "knowledge_missed"}
            return {"status": "ready", "strategy": "placeholder"}
        if capability == "sql.generate":
            return {"sql": "select 1 as placeholder_value"}
        if capability == "sql.generate_split":
            return {
                "queries": [
                    {
                        "model_id": 1,
                        "metrics": ["占位指标一"],
                        "dimensions": [],
                        "sql": "select 1 as placeholder_value",
                        "datasource_id": 1,
                    },
                    {
                        "model_id": 2,
                        "metrics": ["占位指标二"],
                        "dimensions": [],
                        "sql": "select 2 as placeholder_value",
                        "datasource_id": 1,
                    },
                ],
                "strategy": "placeholder",
                "explanation": "占位跨模型 SQL",
            }
        if capability == "sql.validate":
            return {"valid": True, "reason": "placeholder_valid"}
        if capability == "permission.apply":
            return {"allowed": True, "reason": "placeholder_allowed"}
        if capability == "sql.execute":
            question = self._question_from_any_request(request)
            if any(keyword in question for keyword in ("SQL失败", "执行失败")):
                return {
                    "status": "failed",
                    "error_code": "PLACEHOLDER_SQL_FAILED",
                    "message": "占位 SQL 执行失败",
                }
            return {"status": "succeeded", "rows": [{"placeholder_value": 1}], "row_count": 1, "execution_ms": 1}
        if capability == "answer.generate":
            question = self._question_from_any_request(request)
            return {
                "answer": f"这是图工作流占位回答：{question}",
                "warnings": ["当前为占位能力结果，未连接真实 ChatBI 能力"],
            }
        if capability == "question.classify":
            question = self._question_from_v1_request(request)
            if any(keyword in question for keyword in ("越权", "无权限", "禁止")):
                return {
                    "category": "forbidden",
                    "reason": "placeholder_forbidden",
                    "risk_level": "high",
                    "confidence": 1.0,
                }
            if any(keyword in question for keyword in ("你好", "闲聊", "天气")):
                return {
                    "category": "chitchat",
                    "reason": "placeholder_chitchat",
                    "risk_level": "low",
                    "confidence": 1.0,
                }
            return {
                "category": "data",
                "reason": "placeholder_data_question",
                "risk_level": "low",
                "confidence": 1.0,
            }
        if capability == "answer.reject":
            return {
                "answer": "当前问题无法在权限范围内回答。",
                "warnings": ["placeholder_forbidden"],
                "render_type": "text",
            }
        if capability == "answer.chitchat":
            return {
                "answer": "你好，我可以帮你分析业务数据问题。",
                "warnings": ["placeholder_chitchat"],
                "render_type": "text",
            }
        if capability == "question.rewrite":
            question = self._question_from_v1_request(request)
            variables = request.get("variables", {})
            need_user_input = not variables.get("rewrite_response") and any(
                keyword in question for keyword in ("需要澄清", "信息不足", "补充")
            )
            return {
                "rewritten_question": question,
                "need_user_input": need_user_input,
                "missing_slots": ["metric"] if need_user_input else [],
                "image_profile_hint": "placeholder_profile",
            }
        if capability == "question.draw_image_profile":
            return {"profile": "default_table_chart", "chart_candidates": ["table", "line"]}
        if capability == "intent.recognize":
            question = self._question_from_v1_request(request)
            variables = request.get("variables", {})
            ambiguous = not variables.get("intent_response") and any(
                keyword in question for keyword in ("意图不明", "哪个", "不确定")
            )
            return {
                "intent_type": "metric_query",
                "confidence": 0.5 if ambiguous else 0.95,
                "metric_mentions": [],
                "dimension_mentions": [],
                "time_mentions": [],
                "filter_mentions": [],
                "required_slot_types": ["metric"],
                "query_shape": {"select_mode": "aggregate"},
                "ambiguous_slots": ["metric"] if ambiguous else [],
                "conflict_slots": [],
                "validation": {
                    "status": "valid",
                    "reason_code": "INTENT_VALID",
                    "repair_hint": None,
                    "retryable": False,
                    "retry_count": 0,
                    "max_retry_count": 2,
                    "violations": [],
                    "clarification_required": False,
                    "slot_issues": [],
                },
            }
        if capability == "knowledge.retrieve":
            question = self._question_from_v1_request(request)
            variables = request.get("variables", {})
            if any(keyword in question for keyword in ("知识未命中", "不存在")):
                return {
                    "hit": False,
                    "status": "missed",
                    "tables": [],
                    "fields": [],
                    "metrics": [],
                    "terms": [],
                    "examples": [],
                    "ambiguities": [],
                }
            if not variables.get("metric_selection") and any(keyword in question for keyword in ("多指标", "指标歧义")):
                return {
                    "hit": True,
                    "status": "metric_ambiguous",
                    "tables": ["orders"],
                    "fields": ["amount", "profit"],
                    "metrics": ["sales_amount", "gross_profit"],
                    "terms": [],
                    "examples": [],
                    "ambiguities": [{"type": "metric", "candidates": ["sales_amount", "gross_profit"]}],
                }
            return {
                "hit": True,
                "status": "hit",
                "tables": ["orders"],
                "fields": ["amount", "created_at"],
                "metrics": ["sales_amount"],
                "terms": ["销售额"],
                "examples": ["select sum(amount) from orders"],
                "ambiguities": [],
            }
        if capability == "interaction.ask_metric_selection":
            return self._interaction().ask_metric_selection(request)
        if capability == "interaction.ask_cross_model_split":
            return self._interaction().ask_cross_model_split(request)
        if capability == "interaction.ask_rewrite_clarification":
            return self._interaction().ask_rewrite_clarification(request)
        if capability == "interaction.ask_intent_clarification":
            return self._interaction().ask_intent_clarification(request)
        if capability == "interaction.ask_slot_clarification":
            return self._interaction().ask_slot_clarification(request)
        if capability == "sql.handle_error":
            execution = request.get("variables", {}).get("sql_execution", {})
            return {
                "error_code": execution.get("error_code", "SQL_EXECUTION_FAILED"),
                "message": execution.get("message", "SQL 执行失败"),
                "retryable": False,
                "repair_hint": "placeholder_repair_hint",
            }
        if capability == "sql.execute_split":
            return {
                "status": "succeeded",
                "rows": [],
                "row_count": 0,
                "fields": [],
                "execution_ms": 0,
                "sampled_row_count": 0,
                "result_truncated": False,
                "artifact_ref": None,
                "error_code": None,
                "message": None,
            }
        if capability == "execution.validate":
            return validate_execution_output(request.get("variables", {}).get("execution", {}))
        if capability == "question.recommend":
            return {"questions": ["按月查看销售额趋势", "查看销售额最高的商品"]}
        if capability == "answer.compose":
            variables = request.get("variables", {})
            answer = variables.get("answer", {})
            return {
                "final_answer": answer.get("answer", "这是 ChatBI v1 占位最终回复。"),
                "recommendations": variables.get("recommendations", {}).get("questions", []),
                "chart": variables.get("image_profile", {}),
                "metadata": {"source": "placeholder_chatbi_v1"},
            }
        raise ValueError(f"UNKNOWN_PLACEHOLDER_CAPABILITY: {capability}")

    def _question_from_v1_request(self, request: dict[str, Any]) -> str:
        """从 v1 通用节点请求中提取原始问题。"""

        return str(request.get("request", {}).get("question", ""))

    def _interaction(self) -> InteractionAdapter:
        """懒加载交互适配器，兼容测试网关未调用父类初始化的场景。"""

        adapter = getattr(self, "_interaction_adapter", None)
        if adapter is None:
            adapter = InteractionAdapter()
            self._interaction_adapter = adapter
        return adapter

    def _question_from_any_request(self, request: dict[str, Any]) -> str:
        """兼容最小图扁平请求和 v1 通用节点嵌套请求。"""

        if "question" in request:
            return str(request.get("question", ""))
        return self._question_from_v1_request(request)

    def _metric_selection_options(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """从知识检索歧义结果中生成指标选择项。"""

        knowledge = request.get("variables", {}).get("knowledge", {})
        for ambiguity in knowledge.get("ambiguities", []) or []:
            if ambiguity.get("type") != "metric":
                continue
            return [self._metric_option(candidate) for candidate in ambiguity.get("candidates", [])]
        return []

    @staticmethod
    def _metric_option(candidate: Any) -> dict[str, Any]:
        if isinstance(candidate, dict):
            label = (
                candidate.get("display_name")
                or candidate.get("name")
                or candidate.get("biz_name")
                or candidate.get("title")
                or str(candidate.get("asset_id") or "")
            )
            value = candidate.get("asset_id") or candidate.get("biz_name") or label
            return {"label": str(label), "value": value}
        text = str(candidate)
        return {"label": text, "value": text}
