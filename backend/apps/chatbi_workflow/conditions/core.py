from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.execution import NodeExecutionResult
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.runtime.router import ConditionDecision


class SqlValidCondition:
    """SQL 校验通过时才允许进入权限节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        valid = bool(context.variables.get("sql_validation", {}).get("valid"))
        return ConditionDecision(
            matched=valid,
            reason_code="SQL_VALID" if valid else "SQL_INVALID",
            reason_summary="SQL 校验通过" if valid else "SQL 校验未通过",
        )


class PermissionAllowedCondition:
    """权限允许时才允许进入 SQL 执行节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        allowed = bool(context.variables.get("permission", {}).get("allowed"))
        return ConditionDecision(
            matched=allowed,
            reason_code="PERMISSION_ALLOWED" if allowed else "PERMISSION_DENIED",
            reason_summary="权限校验通过" if allowed else "权限校验拒绝",
        )


class QuestionForbiddenCondition:
    """问题分类为越权或禁止回答时进入拒绝分支。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        category = context.variables.get("classification", {}).get("category")
        matched = category == "forbidden"
        return ConditionDecision(
            matched=matched,
            reason_code="QUESTION_FORBIDDEN" if matched else "QUESTION_ALLOWED",
            reason_summary="问题被分类为禁止回答" if matched else "问题未被分类为禁止回答",
        )


class QuestionChitchatCondition:
    """问题分类为闲聊时进入闲聊回复分支。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        category = context.variables.get("classification", {}).get("category")
        matched = category == "chitchat"
        return ConditionDecision(
            matched=matched,
            reason_code="QUESTION_CHITCHAT" if matched else "QUESTION_NOT_CHITCHAT",
            reason_summary="问题被分类为闲聊" if matched else "问题不是闲聊",
        )


class QuestionDataOrFollowupCondition:
    """问题分类为数据问题或追问时进入 ChatBI 主链路。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        category = context.variables.get("classification", {}).get("category")
        matched = category in {"data", "followup"}
        return ConditionDecision(
            matched=matched,
            reason_code="QUESTION_DATA_OR_FOLLOWUP" if matched else "QUESTION_NOT_DATA",
            reason_summary="问题需要进入数据问答链路" if matched else "问题不需要进入数据问答链路",
        )


class RewriteNeedUserInputCondition:
    """问题重写缺少必要槽位时暂停等待用户补充。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        need_user_input = bool(context.variables.get("rewrite", {}).get("need_user_input"))
        return ConditionDecision(
            matched=need_user_input,
            reason_code="REWRITE_NEED_USER_INPUT" if need_user_input else "REWRITE_READY",
            reason_summary="问题重写需要用户补充" if need_user_input else "问题重写信息充分",
        )


class IntentAmbiguousCondition:
    """意图识别不明确时进入意图澄清。"""

    _INTENT_SLOT_NAMES = {"intent", "intent_type", "analysis_type", "analysis_mode", "query_shape"}

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        intent = context.variables.get("intent", {})
        confidence = float(intent.get("confidence", 1.0))
        ambiguous_slots = {str(slot) for slot in intent.get("ambiguous_slots") or []}
        conflict_slots = {str(slot) for slot in intent.get("conflict_slots") or []}
        intent_type = str(intent.get("intent_type") or "").strip()
        matched = bool(
            confidence < 0.8
            or intent_type in {"", "unknown"}
            or ambiguous_slots.intersection(self._INTENT_SLOT_NAMES)
            or conflict_slots.intersection(self._INTENT_SLOT_NAMES)
        )
        return ConditionDecision(
            matched=matched,
            reason_code="INTENT_AMBIGUOUS" if matched else "INTENT_CLEAR",
            reason_summary="意图存在歧义" if matched else "意图识别明确",
        )


class SlotClarificationNeededCondition:
    """意图后处理判定需要补槽时进入槽位澄清。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        variables = context.variables
        slot_response = variables.get("slot_response")
        if isinstance(slot_response, dict) and slot_response.get("skipped") is not True:
            return ConditionDecision(
                matched=False,
                reason_code="SLOT_CLARIFICATION_ANSWERED",
                reason_summary="用户已补充槽位信息",
            )

        intent = variables.get("intent", {})
        validation = intent.get("validation") if isinstance(intent, dict) and isinstance(intent.get("validation"), dict) else {}
        matched = bool(validation.get("clarification_required"))
        return ConditionDecision(
            matched=matched,
            reason_code="SLOT_CLARIFICATION_NEEDED" if matched else "SLOT_CLARIFICATION_NOT_NEEDED",
            reason_summary="需要用户补充槽位信息" if matched else "无需用户补充槽位信息",
        )


class KnowledgeMissedCondition:
    """知识库没有命中时跳过 SQL，直接生成解释性回复。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        knowledge = context.variables.get("knowledge", {})
        matched = knowledge.get("hit") is False and knowledge.get("status") == "missed"
        return ConditionDecision(
            matched=matched,
            reason_code="KNOWLEDGE_MISSED" if matched else "KNOWLEDGE_NOT_MISSED",
            reason_summary="知识库未命中" if matched else "知识库不是未命中状态",
        )


class KnowledgeMetricAmbiguousCondition:
    """知识命中但指标候选不唯一时进入指标选择。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        knowledge = context.variables.get("knowledge", {})
        matched = bool(knowledge.get("ambiguities")) or knowledge.get("status") == "metric_ambiguous"
        return ConditionDecision(
            matched=matched,
            reason_code="KNOWLEDGE_METRIC_AMBIGUOUS" if matched else "KNOWLEDGE_METRIC_CLEAR",
            reason_summary="指标候选存在歧义" if matched else "指标候选明确",
        )


class KnowledgeCrossModelCondition:
    """多个模型的指标必须先确认是否拆分查询。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        knowledge = context.variables.get("knowledge", {})
        plans = knowledge.get("multi_query_plans")
        matched = knowledge.get("status") == "cross_model" and isinstance(plans, list) and len(plans) > 1
        return ConditionDecision(
            matched=matched,
            reason_code="KNOWLEDGE_CROSS_MODEL" if matched else "KNOWLEDGE_SINGLE_MODEL",
            reason_summary="指标来自不同模型，需要确认拆分查询" if matched else "指标属于单一模型",
        )


class KnowledgeHitCondition:
    """知识库命中且无指标歧义时进入 SQL 生成。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        knowledge = context.variables.get("knowledge", {})
        matched = bool(knowledge.get("hit")) and not bool(knowledge.get("ambiguities"))
        return ConditionDecision(
            matched=matched,
            reason_code="KNOWLEDGE_HIT" if matched else "KNOWLEDGE_NOT_HIT",
            reason_summary="知识库命中" if matched else "知识库未准备好生成 SQL",
        )


class InteractionAnsweredCondition:
    """交互节点恢复后，如果用户给出有效回答则回到业务链路。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        variables = context.variables
        matched = any(
            value
            for value in (
                variables.get("rewrite_response"),
                variables.get("intent_response"),
                variables.get("metric_selection"),
                variables.get("slot_response"),
                variables.get("cross_model_response"),
            )
            if not (isinstance(value, dict) and value.get("skipped") is True)
        )
        return ConditionDecision(
            matched=matched,
            reason_code="INTERACTION_ANSWERED" if matched else "INTERACTION_NOT_ANSWERED",
            reason_summary="用户已补充信息" if matched else "用户尚未补充信息",
        )


class InteractionSkippedCondition:
    """用户显式跳过澄清时进入兜底回复。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        variables = context.variables
        responses = [
            variables.get("rewrite_response"),
            variables.get("intent_response"),
            variables.get("metric_selection"),
            variables.get("slot_response"),
            variables.get("cross_model_response"),
        ]
        matched = any(isinstance(value, dict) and value.get("skipped") is True for value in responses)
        return ConditionDecision(
            matched=matched,
            reason_code="INTERACTION_SKIPPED" if matched else "INTERACTION_NOT_SKIPPED",
            reason_summary="用户跳过澄清" if matched else "用户未跳过澄清",
        )


class CrossModelSplitRequestedCondition:
    """用户确认拆分跨模型查询时进入分步执行节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        response = context.variables.get("cross_model_response")
        action = response.get("cross_model_action") if isinstance(response, dict) else None
        matched = action == "split"
        return ConditionDecision(
            matched=matched,
            reason_code="CROSS_MODEL_SPLIT_REQUESTED" if matched else "CROSS_MODEL_SPLIT_NOT_REQUESTED",
            reason_summary="用户确认拆分查询" if matched else "用户未确认拆分查询",
        )


class SqlExecutionSucceededCondition:
    """SQL 执行成功后进入答案生成。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        execution = context.variables.get("sql_execution") or context.variables.get("sql_result", {})
        matched = execution.get("status", "succeeded") == "succeeded"
        return ConditionDecision(
            matched=matched,
            reason_code="SQL_EXECUTION_SUCCEEDED" if matched else "SQL_EXECUTION_NOT_SUCCEEDED",
            reason_summary="SQL 执行成功" if matched else "SQL 执行未成功",
        )


class SqlExecutionFailedCondition:
    """SQL 执行返回失败状态时进入异常处理。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        execution = context.variables.get("sql_execution", {})
        matched = execution.get("status") == "failed"
        return ConditionDecision(
            matched=matched,
            reason_code="SQL_EXECUTION_FAILED" if matched else "SQL_EXECUTION_NOT_FAILED",
            reason_summary="SQL 执行失败" if matched else "SQL 执行未失败",
        )


class SqlErrorRetryableCondition:
    """SQL 错误可通过重新生成 SQL 修复时，回到 SQL 生成节点。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        sql_error = context.variables.get("sql_error", {})
        repair_plan = sql_error.get("repair_plan") if isinstance(sql_error.get("repair_plan"), dict) else {}
        matched = bool(sql_error.get("retryable")) and repair_plan.get("action") == "regenerate_sql"
        return ConditionDecision(
            matched=matched,
            reason_code="SQL_ERROR_RETRYABLE" if matched else "SQL_ERROR_NOT_RETRYABLE",
            reason_summary="SQL 错误可通过重新生成 SQL 重试" if matched else "SQL 错误不可自动重试",
        )


class PlanInfeasibleCondition:
    """查询计划判定不可行时转入解释性回答，禁止带病进入 SQL 生成。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        plan = context.variables.get("plan")
        matched = isinstance(plan, dict) and plan.get("status") == "infeasible"
        summary = "查询计划可执行"
        if matched:
            summary = f"查询计划不可行（{plan.get('infeasible_reason') or 'unknown'}），转入解释性回答"
        return ConditionDecision(
            matched=matched,
            reason_code="PLAN_INFEASIBLE" if matched else "PLAN_FEASIBLE",
            reason_summary=summary,
        )


class NodeDegradedCondition:
    """任一能力节点发生业务失败（node_failure 已写入）时命中，转入解释性回答。"""

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        failure = context.variables.get("node_failure")
        matched = isinstance(failure, dict) and bool(failure.get("error_code"))
        summary = "无节点失败记录"
        if matched:
            summary = f"节点 {failure.get('node')} 失败（{failure.get('error_code')}），转入解释性回答"
        return ConditionDecision(
            matched=matched,
            reason_code="NODE_DEGRADED" if matched else "NODE_NOT_DEGRADED",
            reason_summary=summary,
        )


class InteractionResponseAnsweredCondition:
    """指定交互节点的回答已给出且未跳过时命中。

    与全局 InteractionAnsweredCondition 不同，本条件只看单个响应变量，
    避免早前交互的残留回答污染后续交互节点的路由（多轮澄清中"跳过"失效）。
    """

    def __init__(self, response_key: str, label: str) -> None:
        self._response_key = response_key
        self._label = label

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        value = context.variables.get(self._response_key)
        skipped = isinstance(value, dict) and value.get("skipped") is True
        matched = bool(value) and not skipped
        return ConditionDecision(
            matched=matched,
            reason_code="INTERACTION_ANSWERED" if matched else "INTERACTION_NOT_ANSWERED",
            reason_summary=f"用户已回答{self._label}" if matched else f"用户尚未回答{self._label}",
        )


class InteractionResponseSkippedCondition:
    """指定交互节点被用户显式跳过时命中。"""

    def __init__(self, response_key: str, label: str) -> None:
        self._response_key = response_key
        self._label = label

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        value = context.variables.get(self._response_key)
        matched = isinstance(value, dict) and value.get("skipped") is True
        return ConditionDecision(
            matched=matched,
            reason_code="INTERACTION_SKIPPED" if matched else "INTERACTION_NOT_SKIPPED",
            reason_summary=f"用户跳过了{self._label}" if matched else f"用户未跳过{self._label}",
        )


class ClarificationRoundGate:
    """在既有条件之上限制某个澄清节点的提问轮次。

    exhausted=False：条件命中且轮次未用尽时匹配（允许继续提问）。
    exhausted=True：条件命中但轮次已用尽时匹配（改走兜底回答，而不是撞上
    LOOP_ITERATION_LIMIT_EXCEEDED 导致整个 Run 失败）。
    """

    def __init__(self, inner, ask_node: str, max_rounds: int = 2, exhausted: bool = False) -> None:
        self._inner = inner
        self._ask_node = ask_node
        self._max_rounds = max_rounds
        self._exhausted = exhausted

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        inner_decision = self._inner.evaluate(context, result)
        rounds = int(context.control.loop_iterations.get(self._ask_node, 0))
        within_budget = rounds < self._max_rounds
        if self._exhausted:
            matched = inner_decision.matched and not within_budget
            return ConditionDecision(
                matched=matched,
                reason_code="CLARIFICATION_ROUNDS_EXHAUSTED" if matched else inner_decision.reason_code,
                reason_summary=(
                    f"{self._ask_node} 已提问 {rounds} 轮仍未消除歧义，转入兜底回答"
                    if matched
                    else inner_decision.reason_summary
                ),
            )
        matched = inner_decision.matched and within_budget
        return ConditionDecision(
            matched=matched,
            reason_code=inner_decision.reason_code if matched else (
                "CLARIFICATION_ROUNDS_EXHAUSTED" if inner_decision.matched else inner_decision.reason_code
            ),
            reason_summary=inner_decision.reason_summary,
        )


def register_chatbi_conditions(registry: ConditionRegistry) -> None:
    """注册 ChatBI 图使用的确定性条件。"""

    registry.register("sql.valid", SqlValidCondition())
    registry.register("permission.allowed", PermissionAllowedCondition())
    registry.register("question.forbidden", QuestionForbiddenCondition())
    registry.register("question.chitchat", QuestionChitchatCondition())
    registry.register("question.data_or_followup", QuestionDataOrFollowupCondition())
    registry.register("rewrite.need_user_input", RewriteNeedUserInputCondition())
    registry.register("intent.ambiguous", IntentAmbiguousCondition())
    registry.register("slot.clarification_needed", SlotClarificationNeededCondition())
    registry.register("knowledge.missed", KnowledgeMissedCondition())
    registry.register("knowledge.metric_ambiguous", KnowledgeMetricAmbiguousCondition())
    registry.register("knowledge.cross_model", KnowledgeCrossModelCondition())
    registry.register("knowledge.hit", KnowledgeHitCondition())
    registry.register("interaction.answered", InteractionAnsweredCondition())
    registry.register("interaction.skipped", InteractionSkippedCondition())
    registry.register("cross_model.split_requested", CrossModelSplitRequestedCondition())
    registry.register("sql.execution_succeeded", SqlExecutionSucceededCondition())
    registry.register("sql.execution_failed", SqlExecutionFailedCondition())
    registry.register("sql.error_retryable", SqlErrorRetryableCondition())
    registry.register("node.degraded", NodeDegradedCondition())
    registry.register("plan.infeasible", PlanInfeasibleCondition())

    # 交互回答的作用域化条件：每个交互节点只消费自己的回答。
    scoped_responses = {
        "rewrite": ("rewrite_response", "补充问题澄清"),
        "intent": ("intent_response", "分析方式澄清"),
        "slot": ("slot_response", "槽位澄清"),
        "metric": ("metric_selection", "指标选择"),
        "cross_model": ("cross_model_response", "跨模型拆分确认"),
    }
    for name, (response_key, label) in scoped_responses.items():
        registry.register(
            f"interaction.{name}.answered",
            InteractionResponseAnsweredCondition(response_key, label),
        )
        registry.register(
            f"interaction.{name}.skipped",
            InteractionResponseSkippedCondition(response_key, label),
        )

    # 澄清轮次门控：入口条件命中但轮次用尽时改走兜底回答。
    clarification_gates = {
        "rewrite": (RewriteNeedUserInputCondition(), "ask_rewrite_clarification"),
        "intent": (IntentAmbiguousCondition(), "ask_intent_clarification"),
        "slot": (SlotClarificationNeededCondition(), "ask_slot_clarification"),
        "metric": (KnowledgeMetricAmbiguousCondition(), "ask_metric_selection"),
        "cross_model": (KnowledgeCrossModelCondition(), "ask_cross_model_split"),
    }
    for name, (inner, ask_node) in clarification_gates.items():
        registry.register(
            f"clarify.{name}.allowed",
            ClarificationRoundGate(inner, ask_node, exhausted=False),
        )
        registry.register(
            f"clarify.{name}.exhausted",
            ClarificationRoundGate(inner, ask_node, exhausted=True),
        )
