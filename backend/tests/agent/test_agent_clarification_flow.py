"""P1 交互：clarify 挂起/恢复、澄清预算、上下文折叠、prompt 注入。"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.ai_model.openai.llm import BaseChatOpenAI
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentConfig,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    DimensionSlot,
)
from apps.chatbi.orchestration.agent.composition import build_agent_loop
from apps.chatbi.orchestration.agent.messages import (
    FOLDED_PLACEHOLDER,
    AgentMessage,
    AgentMessageRole,
    fold_tool_messages,
)
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.prompts import build_system_prompt
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.conversation.models import ChatRecord
from apps.retrieval.models.dto import (
    AssetReference,
    RetrievalAmbiguity,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalIntent,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalSlotDecision,
    RetrievalSourceType,
)
from apps.retrieval.query.semantic_binding import SEMANTIC_BINDING_STRATEGY_VERSION
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.temporal import build_temporal_context
from apps.tool import ToolRegistry
from tests.agent.test_agent_loop import (
    FakeSession,
    FinishProbeTool,
    ProbeTool,
    ScriptedModel,
    StaticUnderstandingService,
    _event_domains,
    _tool_message,
)


def _registry_with_clarify():
    registry = ToolRegistry()
    registry.register(ProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    return registry


def _run_and_record():
    run = ChatbiAgentRun(
        oid=1,
        chat_id=1,
        record_id=2,
        status=AgentRunStatus.CREATED.value,
        temporal_context=build_temporal_context().model_dump(mode="json"),
    )
    run.id = 100
    record = ChatRecord(chat_id=1, question="额度趋势", datasource=5)
    record.id = 2
    return run, record


def _ambiguous_store_understanding_state():
    return {
        "question": "今天店铺的客户数",
        "question_understanding": {
            "original_question": "今天店铺的客户数",
            "message_type": "new_question",
            "rewritten_question": "今天店铺的客户数",
            "inherited_context": {},
            "intent": {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["客户数"],
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "ambiguous",
                    }
                ],
                "query_shape": {"select_mode": "aggregate"},
                "ambiguous_slots": ["店铺"],
            },
            "validation": {
                "status": "clarification_required",
                "reason_codes": ["intent_ambiguous", "dimension_role_ambiguous"],
                "clarification_slots": ["dimension"],
            },
        },
    }


def _semantic_hit(asset: AssetReference, title: str) -> RetrievalHit:
    return RetrievalHit(
        resource_id=f"{asset.asset_type.value}:{asset.asset_id}",
        resource_type=asset.asset_type,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id="headless:dataset:3",
        source_resource_id=f"{asset.asset_type.value}:{asset.asset_id}",
        unit_id=f"unit:{asset.asset_type.value}:{asset.asset_id}",
        content_kind="identity",
        title=title,
        source_version="generation-1",
        asset_ref=asset,
    )


def _loop(model, config=None):
    return build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=6),
        model_client=model,
        registry=_registry_with_clarify(),
        understanding_service=StaticUnderstandingService(rewritten_question="额度趋势"),
    )


def test_clarify_suspends_run_and_persists_messages():
    model = ScriptedModel(
        [
            _tool_message("probe", {"value": "warm"}),
            _tool_message(
                "clarify",
                {
                    "question": "你要查哪种额度？",
                    "options": [{"label": "授信额度", "value": "credit"}],
                },
                "c2",
            ),
        ]
    )
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    domains = _event_domains(events)

    assert domains[-1] == "clarification.required"
    assert not {"run.finished", "run.failed"}.intersection(domains)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    payload = events[-1].content
    assert payload["question"] == "你要查哪种额度？"
    assert payload["options"][0]["label"] == "授信额度"
    assert run.status == AgentRunStatus.WAITING_USER.value
    assert record.status == "waiting_user"
    assert record.finish is False
    assert record.finish_time is None
    assert run.budget_snapshot["clarifications"] == 1
    # 消息历史保留了带未回填 tool_call 的 assistant 消息
    assert run.messages[-1]["role"] == "assistant"
    # 派生状态随挂起持久化（不含全量数据），恢复后回填避免重复检索
    assert run.derived_state["last_execution"]["sql"] == "select 1"
    assert "full_data" not in run.derived_state


def test_dimension_role_ambiguity_suspends_before_agent_planning_and_retrieval():
    class AmbiguousDimensionUnderstandingService(StaticUnderstandingService):
        def understand(
            self,
            *,
            question,
            datasource_id,
            conversation_context=None,
            tenant_id=None,
            dataset_id=None,
            temporal_context=None,
        ):
            outcome = super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )
            output = outcome.output.model_copy(
                update={
                    "rewritten_question": "今天店铺的客户数",
                    "intent": outcome.output.intent.model_copy(
                        update={
                            "metric_mentions": ["客户数"],
                            "dimension_mentions": ["店铺"],
                            "dimension_slots": [
                                DimensionSlot(
                                    name="店铺",
                                    role="ambiguous",
                                    value=None,
                                    value_status="ambiguous",
                                )
                            ],
                            "ambiguous_slots": ["店铺"],
                        }
                    ),
                    "validation": outcome.output.validation.model_copy(
                        update={
                            "status": "clarification_required",
                            "reason_codes": [
                                "intent_ambiguous",
                                "dimension_role_ambiguous",
                            ],
                            "clarification_slots": ["dimension"],
                        }
                    ),
                }
            )
            return outcome.__class__(
                output=output, usage_metadata=outcome.usage_metadata
            )

    model = ScriptedModel([])
    run, record = _run_and_record()
    record.question = "今天店铺的客户数"
    session = FakeSession()
    loop = build_agent_loop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=_registry_with_clarify(),
        understanding_service=AmbiguousDimensionUnderstandingService(),
    )

    events = list(loop.run(run, record))
    domains = _event_domains(events)

    assert model.calls == []
    assert "tool.completed" not in domains
    assert domains[-4:] == [
        "step.started",
        "reasoning.snapshot",
        "workflow.step",
        "clarification.required",
    ]
    assert run.status == AgentRunStatus.WAITING_USER.value
    assert run.budget_snapshot["steps"] == 1
    assert run.budget_snapshot["clarifications"] == 1
    # 工作流澄清没有伪造助手工具调用，挂起前只保留规范化后的用户问题。
    assert [message["role"] for message in run.messages] == ["user"]
    clarification_payload = events[-1].content
    assert clarification_payload["question"] == "请确认“店铺”在本次查询中的使用方式。"
    assert [item["value"] for item in clarification_payload["options"]] == [
        "group_by:店铺",
        "filter:店铺",
        "ignore:店铺",
    ]
    clarification = next(
        item for item in session.added if isinstance(item, ChatbiAgentClarification)
    )
    assert (
        clarification.resume_kind
        == AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value
    )
    assert clarification.resume_payload == {
        "operation": "set_dimension_role",
        "slot_name": "店铺",
    }


def test_openai_payload_preserves_reasoning_content_for_tool_call_history():
    model = BaseChatOpenAI(model="test-model", api_key="test-key")
    messages = [
        HumanMessage(content="今天店铺的客户数"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "需要先确认店铺的使用方式。"},
            tool_calls=[
                {
                    "name": "clarify",
                    "args": {"question": "店铺用于分组还是筛选？"},
                    "id": "clarify-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="按店铺分组", tool_call_id="clarify-1"),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "需要先确认店铺的使用方式。"
    assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == "clarify"


def test_openai_payload_does_not_add_reasoning_content_to_regular_message():
    model = BaseChatOpenAI(model="test-model", api_key="test-key")

    payload = model._get_request_payload(
        [HumanMessage(content="你好"), AIMessage(content="你好")]
    )

    assert "reasoning_content" not in payload["messages"][1]


def test_resume_restores_derived_state_into_tool_context():
    run, record = _run_and_record()
    run.messages = [AgentMessage.user("q").model_dump(mode="json")]
    run.derived_state = {
        "semantic_asset_ids": [7, 8],
        "allowed_tables": ["t1"],
        "question": "q",
    }
    captured = {}

    class StateProbeTool(ProbeTool):
        name = "probe"

        def execute(self, ctx, args):
            captured.update(ctx.state)
            return super().execute(ctx, args)

    registry = ToolRegistry()
    registry.register(StateProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    model = ScriptedModel(
        [
            _tool_message("probe", {"value": "x"}),
            _tool_message("finish", {"value": ""}, "c9"),
        ]
    )
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=registry,
        understanding_service=StaticUnderstandingService(),
    )
    clarification = SimpleNamespace(
        tool_call_id="prev",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "答"},
        question="请补充信息",
        options=[],
    )
    list(loop.resume(run, record, clarification, "答"))

    assert captured["semantic_asset_ids"] == [7, 8]
    assert captured["allowed_tables"] == ["t1"]


def test_resume_applies_structured_semantic_clarification_to_trusted_scope():
    metric = AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=274,
        model_id=10,
    )
    other_metric = AssetReference(
        asset_type=RetrievalResourceType.METRIC,
        asset_id=273,
        model_id=11,
    )
    dimension = AssetReference(
        asset_type=RetrievalResourceType.DIMENSION,
        asset_id=280,
        model_id=10,
    )
    other_dimension = AssetReference(
        asset_type=RetrievalResourceType.DIMENSION,
        asset_id=283,
        model_id=11,
    )
    bundle = RetrievalBundle(
        request_id="run-216-retrieval",
        bindings=RetrievalBindings(
            metrics=[
                _semantic_hit(metric, "总下单客户数"),
                _semantic_hit(other_metric, "支付客户数"),
            ],
            dimensions=[
                _semantic_hit(dimension, "店铺名称"),
                _semantic_hit(other_dimension, "店铺名称"),
            ],
        ),
        decision=RetrievalDecision(
            status=RetrievalDecisionStatus.AMBIGUOUS,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:1",
                    purpose=RetrievalPurpose.METRIC,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=[metric, other_metric],
                ),
                RetrievalSlotDecision(
                    subquery_id="dimension:1",
                    purpose=RetrievalPurpose.DIMENSION,
                    status=RetrievalDecisionStatus.AMBIGUOUS,
                    candidate_assets=[dimension, other_dimension],
                ),
            ],
            ambiguities=[
                RetrievalAmbiguity(
                    subquery_id="metric:1",
                    reason_code="MULTIPLE_IDENTITY_MATCHES",
                    candidate_assets=[metric, other_metric],
                ),
                RetrievalAmbiguity(
                    subquery_id="dimension:1",
                    reason_code="MULTIPLE_IDENTITY_MATCHES",
                    candidate_assets=[dimension, other_dimension],
                ),
            ],
            reason_codes=["SEMANTIC_BINDING_AMBIGUOUS"],
        ),
        diagnostics=RetrievalDiagnostics(
            strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
            index_generation="generation-1",
        ),
    )
    request = RetrievalRequest(
        request_id=bundle.request_id,
        tenant_id=1,
        actor_id=1,
        original_question="今天店铺的客户数",
        rewritten_question="今天店铺的客户数",
        intent=RetrievalIntent(
            intent_type="metric_query",
            metric_mentions=["客户数"],
            dimension_mentions=["店铺"],
            dimension_slots=[{"name": "店铺", "role": "group_by"}],
            time_mentions=["今天"],
            time_range={
                "raw": "今天",
                "value_status": "provided",
                "normalized": {
                    "kind": "absolute_range",
                    "start": "2026-07-31",
                    "end_exclusive": "2026-08-01",
                    "timezone": "Asia/Shanghai",
                    "source_raw": "今天",
                },
            },
        ),
        scope=RetrievalScope(dataset_ids=[3]),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version=SEMANTIC_BINDING_STRATEGY_VERSION,
    )
    raw_payload = {
        "hit": True,
        "status": "metric_ambiguous",
        "dataset_id": 3,
        "tables": [],
        "candidate_groups": {
            "metrics": [
                {
                    "asset_type": "METRIC",
                    "asset_id": 274,
                    "model_id": 10,
                    "name": "总下单客户数",
                    "biz_name": "total_order_customers",
                    "payload": {},
                },
                {
                    "asset_type": "METRIC",
                    "asset_id": 273,
                    "model_id": 11,
                    "name": "支付客户数",
                    "biz_name": "paid_customers",
                    "payload": {},
                },
            ],
            "dimensions": [
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 280,
                    "model_id": 10,
                    "name": "店铺名称",
                    "biz_name": "shop_name",
                    "payload": {},
                },
                {
                    "asset_type": "DIMENSION",
                    "asset_id": 283,
                    "model_id": 11,
                    "name": "店铺名称",
                    "biz_name": "store_name",
                    "payload": {},
                },
            ],
        },
        "selected_assets": {"metrics": [], "dimensions": []},
        "decision": {"status": "ambiguous", "reason_codes": []},
        "ambiguities": [],
        "allowed_asset_ids": [],
    }
    run, record = _run_and_record()
    run.status = AgentRunStatus.WAITING_USER.value
    run.messages = [AgentMessage.user("今天店铺的客户数").model_dump(mode="json")]
    run.derived_state = {
        **_ambiguous_store_understanding_state(),
        "semantic_bundle": bundle.model_dump(mode="json"),
        "semantic_payload": raw_payload,
        "semantic_package": raw_payload,
        "semantic_retrieval_request": request.model_dump(mode="json"),
        "semantic_retrieval_filters": {
            "subqueries": [
                {"subquery_id": "metric:1", "required": True},
                {"subquery_id": "dimension:1", "required": True},
            ]
        },
        "semantic_scope": {
            "workspace_id": 1,
            "user_id": 1,
            "datasource_id": 5,
            "dataset_id": 3,
            "retrieval_id": bundle.request_id,
            "decision_status": "ambiguous",
            "allowed_assets": [],
            "authorized_tables": [],
            "normalized_time_range": request.intent.time_range["normalized"],
            "permission_version": None,
        },
    }
    captured = {}

    class StateProbeTool(ProbeTool):
        name = "probe"

        def execute(self, ctx, args):
            captured.update(ctx.state)
            return super().execute(ctx, args)

    registry = ToolRegistry()
    registry.register(StateProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    model = ScriptedModel(
        [
            _tool_message("probe", {"value": "x"}),
            _tool_message("finish", {"value": ""}, "finish-1"),
        ]
    )
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=3,
            data_set_name="测试数据集",
            id=3,
            name="测试数据集",
            biz_name="test_dataset",
            type="DATASET",
        ),
        models=[
            {"id": 10, "tableQuery": "fct_store_order_daily"},
            {"id": 11, "tableQuery": "fct_other_order_daily"},
        ],
        metrics=[
            SchemaElement(
                data_set_id=3,
                data_set_name="测试数据集",
                model=10,
                id=274,
                name="总下单客户数",
                biz_name="total_order_customers",
                type="METRIC",
            ),
            SchemaElement(
                data_set_id=3,
                data_set_name="测试数据集",
                model=11,
                id=273,
                name="支付客户数",
                biz_name="paid_customers",
                type="METRIC",
            ),
        ],
        dimensions=[
            SchemaElement(
                data_set_id=3,
                data_set_name="测试数据集",
                model=10,
                id=280,
                name="店铺名称",
                biz_name="shop_name",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=3,
                data_set_name="测试数据集",
                model=11,
                id=283,
                name="店铺名称",
                biz_name="store_name",
                type="DIMENSION",
            ),
            SchemaElement(
                data_set_id=3,
                data_set_name="测试数据集",
                model=10,
                id=281,
                name="统计日期",
                biz_name="stat_date",
                type="DIMENSION",
                ext_info={
                    "is_default_time": True,
                    "dimension_type": "partition_time",
                    "dimension_data_type": "date",
                },
            ),
        ],
    )
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=registry,
        understanding_service=StaticUnderstandingService(),
        semantic_schema_provider=SimpleNamespace(
            build_dataset_schema=lambda _oid, _dataset_id: schema
        ),
    )
    option = {
        "label": "总下单客户数（按店铺名称分组）",
        "value": "metric-274-dimension-280",
        "bindings": [
            {
                "subquery_id": "metric:1",
                "asset_type": "METRIC",
                "asset_id": 274,
                "model_id": 10,
            },
            {
                "subquery_id": "dimension:1",
                "asset_type": "DIMENSION",
                "asset_id": 280,
                "model_id": 10,
            },
        ],
    }
    clarification = SimpleNamespace(
        tool_call_id="clarify-semantic",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={
            "operation": "resolve_semantic_bindings",
            "retrieval_id": bundle.request_id,
            "options": [option],
        },
        answer={
            "selections": [{"label": option["label"], "value": option["value"]}],
            "text": None,
        },
        question="请选择口径",
        options=[option],
    )

    list(
        loop.resume(
            run,
            record,
            clarification,
            f"用户澄清回答：{option['label']}",
        )
    )

    assert captured["semantic_scope"]["decision_status"] == "resolved"
    assert [
        item["asset_id"] for item in captured["semantic_scope"]["allowed_assets"]
    ] == [274, 280, 281]
    assert captured["semantic_package"]["ambiguities"] == []
    assert captured["semantic_package"]["slot_bindings"]["time_filters"] == [
        {
            "asset_type": "DIMENSION",
            "asset_id": 281,
            "display_name": "统计日期",
            "biz_name": "stat_date",
            "confidence": 1.0,
            "source": "intent_time_range",
            "operator": "=",
            "value": request.intent.time_range["normalized"],
        }
    ]
    assert captured["semantic_scope"]["compile_plan"] == {
        "metric_asset_ids": [274],
        "dimension_asset_ids": [280],
        "filters": [],
        "temporal_plan": {
            "filters": [
                {
                    "asset_id": 281,
                    "operator": "=",
                    "value": request.intent.time_range["normalized"],
                }
            ],
            "time_bucket": None,
        },
        "order_by": [],
        "limit": None,
        "intent_type": "metric_query",
        "query_shape": {"select_mode": "aggregate"},
    }
    first_call = model.calls[0]
    tool_messages = [m for m in first_call if m.role == AgentMessageRole.TOOL]
    assert any('"metric_asset_ids": [274]' in m.content for m in tool_messages)
    assert any('"asset_id": 281' in m.content for m in tool_messages)


def test_resume_continues_from_clarification_to_finish():
    suspend_model = ScriptedModel(
        [
            _tool_message(
                "clarify", {"question": "哪种额度？", "options": []}, "call_clarify"
            ),
        ]
    )
    run, record = _run_and_record()
    list(_loop(suspend_model).run(run, record))
    assert run.status == AgentRunStatus.WAITING_USER.value

    resume_model = ScriptedModel(
        [
            _tool_message("probe", {"value": "x"}),
            _tool_message("finish", {"value": ""}, "c9"),
        ]
    )
    clarification = SimpleNamespace(
        tool_call_id="call_clarify",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "授信额度"},
        question="哪种额度？",
        options=[],
    )
    events = list(
        _loop(resume_model).resume(run, record, clarification, "用户澄清回答：授信额度")
    )
    domains = _event_domains(events)

    assert domains[0] == "clarification.accepted"
    assert domains[-2:] == ["answer.completed", "run.finished"]
    assert run.status == AgentRunStatus.FINISHED.value
    # 恢复后的首轮消息里包含澄清答案 ToolMessage
    first_call = resume_model.calls[0]
    tool_messages = [m for m in first_call if m.role == AgentMessageRole.TOOL]
    assert any("授信额度" in m.content for m in tool_messages)
    # 预算从快照恢复：挂起时 1 步 + 恢复后 2 步
    assert run.budget_snapshot["steps"] == 3
    assert run.budget_snapshot["clarifications"] == 1


def test_resume_emits_acceptance_without_reunderstanding():
    class TrackingUnderstandingService(StaticUnderstandingService):
        def __init__(self):
            super().__init__(rewritten_question="用户澄清后的完整问题")
            self.called = False

        def understand(
            self,
            *,
            question,
            datasource_id,
            conversation_context=None,
            tenant_id=None,
            dataset_id=None,
            temporal_context=None,
        ):
            self.called = True
            return super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )

    run, record = _run_and_record()
    run.status = AgentRunStatus.WAITING_USER.value
    run.messages = [AgentMessage.user("今天店铺的客户数").model_dump(mode="json")]
    run.derived_state = _ambiguous_store_understanding_state()
    understanding_service = TrackingUnderstandingService()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=ScriptedModel([]),
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={
            "selections": [{"label": "按店铺分组", "value": "group_by:店铺"}],
            "text": None,
        },
        question="请确认店铺用法",
        options=[],
    )

    events = loop.resume(run, record, clarification, "按店铺分组")
    first_event = next(events)

    assert _event_domains([first_event]) == ["clarification.accepted"]
    assert not understanding_service.called


def test_filter_role_clarification_resumes_to_targeted_value_clarification():
    run, record = _run_and_record()
    run.status = AgentRunStatus.WAITING_USER.value
    run.messages = [AgentMessage.user("今天店铺的客户数").model_dump(mode="json")]
    run.derived_state = _ambiguous_store_understanding_state()
    session = FakeSession()
    understanding_service = StaticUnderstandingService()
    loop = build_agent_loop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=ScriptedModel([]),
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={
            "selections": [{"label": "筛选具体店铺", "value": "filter:店铺"}],
            "text": None,
        },
        question="请确认店铺用法",
        options=[],
    )

    events = list(loop.resume(run, record, clarification, "用户澄清回答：筛选具体店铺"))

    assert _event_domains(events)[-1] == "clarification.required"
    assert run.status == AgentRunStatus.WAITING_USER.value
    updated_slot = run.derived_state["question_understanding"]["intent"][
        "dimension_slots"
    ][0]
    assert updated_slot["role"] == "filter"
    assert updated_slot["value_status"] == "not_provided"
    next_clarification = [
        item for item in session.added if isinstance(item, ChatbiAgentClarification)
    ][-1]
    assert next_clarification.resume_payload == {
        "operation": "set_dimension_filter_value",
        "slot_name": "店铺",
    }
    next_clarification.answer = {"selections": [], "text": "1号店铺"}
    finish_model = ScriptedModel([AIMessage(content="查询完成")])
    finish_loop = build_agent_loop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=finish_model,
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )

    finish_events = list(
        finish_loop.resume(run, record, next_clarification, "用户澄清回答：1号店铺")
    )

    assert _event_domains(finish_events)[:2] == [
        "clarification.accepted",
        "question.understood",
    ]
    assert run.status == AgentRunStatus.FAILED.value
    confirmed = run.derived_state["question_understanding"]
    assert confirmed["rewritten_question"] == "今天店铺的客户数"
    assert confirmed["intent"]["dimension_slots"][0]["value"] == "1号店铺"
    assert confirmed["validation"]["status"] == "valid"


def test_preflight_value_clarification_targets_the_filter_that_is_missing_value():
    understanding = {
        "validation": {
            "status": "clarification_required",
            "reason_codes": ["dimension_filter_value_missing"],
        },
        "intent": {
            "dimension_slots": [
                {
                    "name": "档口ID",
                    "role": "filter",
                    "value": "100011",
                    "value_status": "provided",
                },
                {
                    "name": "客户ID",
                    "role": "filter",
                    "value": None,
                    "value_status": "not_provided",
                },
            ]
        },
    }

    clarification = AgentInputPreparer._preflight_clarification(understanding)

    assert clarification is not None
    assert clarification.question == "请补充需要筛选的具体客户ID。"
    assert clarification.resume_payload == {
        "operation": "set_dimension_filter_value",
        "slot_name": "客户ID",
    }


def test_preflight_temporal_clarification_blocks_retrieval_until_confirmation():
    understanding = {
        "validation": {
            "status": "clarification_required",
            "reason_codes": ["temporal_clarification_required"],
        },
        "intent": {},
        "temporal_interpretation": {
            "plan": {
                "status": "clarification_required",
                "ambiguities": [
                    {
                        "code": "time_range_amount_missing",
                        "raw": "最近",
                    }
                ],
            }
        },
    }

    clarification = AgentInputPreparer._preflight_clarification(understanding)

    assert clarification is not None
    assert clarification.question == "请提供明确的时间范围。"
    assert clarification.options[0]["label"] == "最近 7 天"
    assert clarification.options[0]["value"]["temporal_confirmation"] == "最近7天"
    assert clarification.options[0]["value"]["temporal_plan"]["status"] == "resolved"
    assert clarification.resume_payload == {"operation": "resolve_temporal_plan"}


def test_clarify_over_budget_rejected_and_loop_continues():
    run, record = _run_and_record()
    # 快照造成澄清已达上限
    run.budget_snapshot = {"clarifications": 2}
    clarification = SimpleNamespace(
        tool_call_id="prev",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "回答"},
        question="请补充信息",
        options=[],
    )
    run.messages = [
        AgentMessage.user("额度趋势").model_dump(mode="json"),
    ]
    resume_model = ScriptedModel(
        [
            _tool_message("clarify", {"question": "再问一次？", "options": []}, "c2"),
            AIMessage(content="好的，基于现有信息直接回答。"),
        ]
    )
    events = list(_loop(resume_model).resume(run, record, clarification, "回答"))
    domains = _event_domains(events)

    assert "clarification.required" not in domains  # 未再次挂起
    # 澄清预算拒绝后没有成功查询结果，不能用普通文本伪装问数成功。
    assert run.status == AgentRunStatus.FAILED.value
    rejected = [
        m
        for m in resume_model.calls[1]
        if m.role == AgentMessageRole.TOOL and "上限" in m.content
    ]
    assert rejected


def test_resume_updates_target_slot_without_rewriting_or_reunderstanding():
    run, record = _run_and_record()
    run.messages = [AgentMessage.user("今天店铺的客户数").model_dump(mode="json")]
    run.derived_state = _ambiguous_store_understanding_state()
    run.derived_state["semantic_asset_ids"] = [272, 276]
    captured_state = {}

    class TrackingUnderstandingService(StaticUnderstandingService):
        def __init__(self):
            super().__init__()
            self.called = False

        def understand(
            self,
            *,
            question,
            datasource_id,
            conversation_context=None,
            tenant_id=None,
            dataset_id=None,
            temporal_context=None,
        ):
            self.called = True
            return super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )

    class StateProbeTool(ProbeTool):
        name = "probe"

        def execute(self, ctx, args):
            captured_state.update(ctx.state)
            return super().execute(ctx, args)

    registry = ToolRegistry()
    registry.register(StateProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    model = ScriptedModel(
        [
            _tool_message("probe", {"value": "x"}),
            _tool_message("finish", {"value": ""}, "c9"),
        ]
    )
    understanding_service = TrackingUnderstandingService()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=registry,
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={
            "selections": [{"label": "按店铺分组", "value": "group_by:店铺"}],
            "text": None,
        },
        question="请确认店铺维度的使用方式",
        options=[{"label": "按店铺分组", "value": "group_by:店铺"}],
    )

    events = list(loop.resume(run, record, clarification, "用户澄清回答：按店铺分组"))

    assert _event_domains(events)[:2] == [
        "clarification.accepted",
        "question.understood",
    ]
    assert not understanding_service.called
    assert not any(message.role == AgentMessageRole.TOOL for message in model.calls[0])
    assert any(
        message.role == AgentMessageRole.USER and message.content == "今天店铺的客户数"
        for message in model.calls[0]
    )
    updated = captured_state["question_understanding"]
    assert updated["validation"]["status"] == "valid"
    assert updated["intent"]["dimension_slots"][0]["role"] == "group_by"
    assert updated["intent"]["query_shape"]["needs_group_by"] is True
    assert updated["rewritten_question"] == "今天店铺的客户数"
    assert run.derived_state["semantic_asset_ids"] == [272, 276]


def test_fold_messages_folds_old_tool_results_only():
    messages = [
        AgentMessage.user("q"),
        AgentMessage.assistant(""),
        AgentMessage.tool("x" * 500, "a"),
        AgentMessage.assistant(""),
        AgentMessage.tool("y" * 500, "b"),
    ]
    fold_tool_messages(messages, max_chars=100, keep_recent=2)
    assert messages[2].content == FOLDED_PLACEHOLDER
    assert messages[4].content == "y" * 500  # 最近窗口不折叠
    assert messages[0].content == "q"  # 非工具消息不折叠


def test_system_prompt_injects_history_and_confirmed_understanding():
    prompt = build_system_prompt(
        datasource_id=5,
        oid=1,
        history_summary="- 问：上月 GMV\n  SQL：select 1\n  答（摘要）：100 万",
        question_understanding={
            "rewritten_question": "查询上月授信额度",
            "intent": {"metric_mentions": ["授信额度"]},
            "validation": {"status": "valid"},
        },
    )
    assert "最近对话" in prompt
    assert "上月 GMV" in prompt
    assert "已确认的问题理解" in prompt
    assert "查询上月授信额度" in prompt
    assert "不得在工具规划阶段再次继承" in prompt


def test_system_prompt_omits_optional_sections():
    prompt = build_system_prompt(datasource_id=5, oid=1)
    assert "最近对话" not in prompt
    assert "## 已确认的问题理解" not in prompt
