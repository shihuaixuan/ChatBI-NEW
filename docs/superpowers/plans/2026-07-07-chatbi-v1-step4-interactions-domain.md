# ChatBI v1 Step 4 Interactions Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ChatBI v1 的交互回答收敛到标准 `variables.interactions.<ask_node>` 域，并把原先运行时 patcher 的业务改写迁移到能力节点消费阶段。

**Architecture:** workflow engine 只负责把用户回答写入上下文：标准交互域写结构化记录，旧 `variables.*_response` 字段继续写裸 response 作为迁移兼容。ChatBI adapter 通过 `ChatBIRunContext` 统一读取标准域，slot clarification 在 knowledge 节点内生成增强 intent 视图，metric selection 在 QueryPlanBinder 中收敛为 plan。ChatBI v1 runtime 不再注入 `ChatBIV1InteractionResponsePatcher`。

**Tech Stack:** Python 3.11、Pydantic v2、SQLModel/SQLAlchemy、pytest、FastAPI test client、GitHub Actions `crate-ci/typos`。

## Global Constraints

- 代码注释必须使用中文。
- bug 定位与修复必须基于真实根因，不能只按单个案例打补丁。
- 保留迁移兼容：旧 `variables.rewrite_response`、`variables.intent_response`、`variables.slot_response`、`variables.metric_selection`、`variables.cross_model_response` 继续可读。
- 不实现 Step 5 的结果校验、比较/占比多查询计划、HAVING/detail 模式。
- 不实现 Step 6 的 trace metadata 与前端 label 全量派生。
- 不删除 `slot_bindings` 双写。
- 每个任务按 TDD 执行，测试通过后提交。

---

## 文件职责

- Create `backend/apps/workflow/capabilities/interactions.py`：交互节点规格、标准路径、兼容路径、标准记录构造、标准/旧字段读取、slot/metric 选择纯函数。
- Modify `backend/apps/workflow/capabilities/context.py`：新增标准交互域 accessor，旧 response 属性改为标准域优先、旧字段回退。
- Modify `backend/apps/workflow_engine/runtime/graph_runtime.py`：resume 写入时对 `variables.interactions.*` 生成结构化交互记录，不再需要 ChatBI patcher。
- Modify `backend/apps/workflow/nodes/v1.py`：交互节点返回标准路径 + 旧路径的 `allowed_update_paths`。
- Modify `backend/apps/workflow/definitions/chatbi_v1.py`：交互节点 metadata 声明 standard/legacy path、response key、max_rounds；handler 注册使用统一交互规格。
- Modify `backend/apps/workflow/conditions/core.py`：作用域化 answered/skipped 和 clarify round gate 从交互规格读取，兼容旧字段。
- Modify `backend/apps/workflow/runtime.py`：移除 `ChatBIV1InteractionResponsePatcher` 类与注入。
- Modify `backend/apps/workflow/capabilities/adapters/knowledge.py`：在 retrieve 内使用 slot clarification response 构造本地 intent 视图。
- Modify `backend/apps/workflow/capabilities/planning.py`：在 QueryPlanBinder 内消费 metric selection response。
- Modify `backend/apps/semantic/assets/quality_service.py`：修正 typos 命中的 `unparsable` 拼写。
- Modify `docs/chatbi-v1-graph-refactor-analysis-and-design.md`：实现完成后标记 Step 4 完成。
- Tests under `backend/tests/workflow` and `backend/tests/workflow_engine`：覆盖标准域、条件、resume、slot/metric 消费、runtime 不再注入 patcher。

---

### Task 1: 交互域 helper 与 ChatBIRunContext 读取器

**Files:**
- Create: `backend/apps/workflow/capabilities/interactions.py`
- Modify: `backend/apps/workflow/capabilities/context.py`
- Test: `backend/tests/workflow/test_run_context.py`
- Test: `backend/tests/workflow/test_interactions_domain.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) InteractionSpec`
  - `CHATBI_V1_INTERACTION_SPECS: dict[str, InteractionSpec]`
  - `standard_interaction_path(node_name: str) -> str`
  - `legacy_interaction_path(node_name: str) -> str | None`
  - `build_interaction_record(node_name: str, response: dict[str, Any], round_count: int, answered_at: datetime) -> dict[str, Any]`
  - `read_interaction_record(variables: dict[str, Any], node_name: str) -> dict[str, Any]`
  - `read_interaction_response(variables: dict[str, Any], node_name: str, legacy_key: str | None = None) -> dict[str, Any]`
  - `ChatBIRunContext.interaction(node_name: str) -> dict[str, Any]`
  - `ChatBIRunContext.interaction_response(node_name: str, legacy_key: str | None = None) -> dict[str, Any]`
- Consumes: existing `ChatBIRunContext.domain()`

- [ ] **Step 1: 写标准交互读取失败测试**

Append to `backend/tests/workflow/test_run_context.py`:

```python
def test_run_context_prefers_standard_interaction_response_over_legacy_key():
    ctx = ChatBIRunContext(
        _request(
            variables={
                "interactions": {
                    "ask_metric_selection": {
                        "node_name": "ask_metric_selection",
                        "round": 1,
                        "response": {"metric": 239},
                        "skipped": False,
                    }
                },
                "metric_selection": {"metric": 999},
            }
        )
    )

    assert ctx.interaction("ask_metric_selection")["round"] == 1
    assert ctx.metric_selection == {"metric": 239}
```

Create `backend/tests/workflow/test_interactions_domain.py`:

```python
from datetime import datetime, timezone

from apps.workflow.capabilities.interactions import (
    build_interaction_record,
    legacy_interaction_path,
    read_interaction_response,
    standard_interaction_path,
)


def test_build_interaction_record_normalizes_skipped_and_timestamp():
    answered_at = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)

    record = build_interaction_record(
        "ask_slot_clarification",
        {"skipped": True},
        round_count=2,
        answered_at=answered_at,
    )

    assert record == {
        "node_name": "ask_slot_clarification",
        "round": 2,
        "response": {"skipped": True},
        "skipped": True,
        "answered_at": "2026-07-07T10:00:00+00:00",
    }


def test_interaction_paths_are_stable_for_metric_selection():
    assert standard_interaction_path("ask_metric_selection") == "variables.interactions.ask_metric_selection"
    assert legacy_interaction_path("ask_metric_selection") == "variables.metric_selection"


def test_read_interaction_response_falls_back_to_legacy_key():
    variables = {"metric_selection": {"metric": 239}}

    response = read_interaction_response(
        variables,
        "ask_metric_selection",
        legacy_key="metric_selection",
    )

    assert response == {"metric": 239}
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_run_context.py::test_run_context_prefers_standard_interaction_response_over_legacy_key tests/workflow/test_interactions_domain.py -q
```

Expected: FAIL，提示 `apps.workflow.capabilities.interactions` 或 `ChatBIRunContext.interaction` 不存在。

- [ ] **Step 3: 实现交互 helper 与 context accessor**

Create `backend/apps/workflow/capabilities/interactions.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class InteractionSpec:
    """ChatBI v1 单个交互节点的上下文写入规格。"""

    node_name: str
    name: str
    legacy_key: str
    max_rounds: int = 2

    @property
    def standard_path(self) -> str:
        return f"variables.interactions.{self.node_name}"

    @property
    def legacy_path(self) -> str:
        return f"variables.{self.legacy_key}"


CHATBI_V1_INTERACTION_SPECS: dict[str, InteractionSpec] = {
    "ask_rewrite_clarification": InteractionSpec(
        node_name="ask_rewrite_clarification",
        name="rewrite",
        legacy_key="rewrite_response",
    ),
    "ask_intent_clarification": InteractionSpec(
        node_name="ask_intent_clarification",
        name="intent",
        legacy_key="intent_response",
    ),
    "ask_slot_clarification": InteractionSpec(
        node_name="ask_slot_clarification",
        name="slot",
        legacy_key="slot_response",
    ),
    "ask_cross_model_split": InteractionSpec(
        node_name="ask_cross_model_split",
        name="cross_model",
        legacy_key="cross_model_response",
    ),
    "ask_metric_selection": InteractionSpec(
        node_name="ask_metric_selection",
        name="metric",
        legacy_key="metric_selection",
    ),
}


def interaction_spec(node_name: str) -> InteractionSpec | None:
    return CHATBI_V1_INTERACTION_SPECS.get(node_name)


def standard_interaction_path(node_name: str) -> str:
    return f"variables.interactions.{node_name}"


def legacy_interaction_path(node_name: str) -> str | None:
    spec = interaction_spec(node_name)
    return spec.legacy_path if spec is not None else None


def build_interaction_record(
    node_name: str,
    response: dict[str, Any],
    round_count: int,
    answered_at: datetime,
) -> dict[str, Any]:
    """构造标准交互记录；裸 response 只保留在兼容旧字段中。"""

    safe_round = max(1, int(round_count or 1))
    return {
        "node_name": node_name,
        "round": safe_round,
        "response": response,
        "skipped": bool(response.get("skipped") is True),
        "answered_at": answered_at.isoformat(),
    }


def read_interaction_record(variables: dict[str, Any], node_name: str) -> dict[str, Any]:
    interactions = variables.get("interactions")
    if not isinstance(interactions, dict):
        return {}
    record = interactions.get(node_name)
    return record if isinstance(record, dict) else {}


def read_interaction_response(
    variables: dict[str, Any],
    node_name: str,
    legacy_key: str | None = None,
) -> dict[str, Any]:
    record = read_interaction_record(variables, node_name)
    response = record.get("response")
    if isinstance(response, dict):
        return response
    if legacy_key:
        legacy_response = variables.get(legacy_key)
        if isinstance(legacy_response, dict):
            return legacy_response
    return {}
```

Modify `backend/apps/workflow/capabilities/context.py`:

```python
from apps.workflow.capabilities.interactions import read_interaction_record, read_interaction_response
```

Add methods and update properties:

```python
    def interaction(self, node_name: str) -> dict[str, Any]:
        return read_interaction_record(self._variables, node_name)

    def interaction_response(self, node_name: str, legacy_key: str | None = None) -> dict[str, Any]:
        return read_interaction_response(self._variables, node_name, legacy_key)

    @property
    def rewrite_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_rewrite_clarification", "rewrite_response")

    @property
    def intent_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_intent_clarification", "intent_response")

    @property
    def slot_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_slot_clarification", "slot_response")

    @property
    def metric_selection(self) -> dict[str, Any]:
        return self.interaction_response("ask_metric_selection", "metric_selection")

    @property
    def cross_model_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_cross_model_split", "cross_model_response")
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_run_context.py tests/workflow/test_interactions_domain.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/workflow/capabilities/interactions.py backend/apps/workflow/capabilities/context.py backend/tests/workflow/test_run_context.py backend/tests/workflow/test_interactions_domain.py
git commit -m "feat: add chatbi interaction domain accessors"
```

---

### Task 2: resume 标准交互域双写

**Files:**
- Modify: `backend/apps/workflow_engine/runtime/graph_runtime.py`
- Modify: `backend/apps/workflow/nodes/v1.py`
- Test: `backend/tests/workflow_engine/test_runtime_interactions.py`
- Test: `backend/tests/workflow/test_v1_flow.py`

**Interfaces:**
- Consumes: `build_interaction_record()` and `standard_interaction_path()`
- Produces: `GraphRuntime.resume()` writes structured records for `variables.interactions.*`, while old paths receive raw response.

- [ ] **Step 1: 写 resume 标准域写入失败测试**

Append to `backend/tests/workflow_engine/test_runtime_interactions.py`:

```python
def test_resume_writes_structured_record_for_standard_interaction_path():
    runtime, store, interactions = _runtime_with_interaction(
        allowed_update_paths=[
            "variables.interactions.ask_metric_selection",
            "variables.metric_selection",
        ]
    )
    run = runtime.create_run(
        "run-standard-interaction",
        "demo",
        "v1",
        WorkflowContext(request={"tenant_id": 1, "user_id": 7}),
    )
    waiting = runtime.execute(run.run_id)

    outcome = runtime.resume(
        waiting.run_id,
        waiting.context.control.pending_interaction_id or "",
        {"metric": 239},
        tenant_id=1,
        user_id=7,
    )

    variables = outcome.context.variables
    record = variables["interactions"]["ask_metric_selection"]
    assert record["node_name"] == "ask_metric_selection"
    assert record["round"] == 1
    assert record["response"] == {"metric": 239}
    assert record["skipped"] is False
    assert "answered_at" in record
    assert variables["metric_selection"] == {"metric": 239}
```

If the existing helper does not accept custom paths, add this focused helper inside the test file:

```python
def _runtime_with_interaction(allowed_update_paths):
    handler = StaticInteractionHandler(
        prompt="请选择指标",
        allowed_update_paths=allowed_update_paths,
        node_name="ask_metric_selection",
    )
    # 复用本文件已有 runtime 装配方式，只替换 interaction handler。
    return _runtime(handler)
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_runtime_interactions.py::test_resume_writes_structured_record_for_standard_interaction_path -q
```

Expected: FAIL，`variables.interactions.ask_metric_selection` 当前被写成裸 response 或不存在。

- [ ] **Step 3: 实现标准路径写入**

Modify `backend/apps/workflow_engine/runtime/graph_runtime.py` imports:

```python
from datetime import datetime, timezone

from apps.workflow.capabilities.interactions import build_interaction_record
```

Replace resume patch creation:

```python
            answered = self._interactions.answer(interaction_id, response)
            answered_at = datetime.now(timezone.utc)
            round_count = int(run.context.control.loop_iterations.get(answered.node_name, 0) or 1)
            patch_values = {}
            for path in answered.allowed_update_paths:
                if path.startswith("variables.interactions."):
                    patch_values[path] = build_interaction_record(
                        answered.node_name,
                        response,
                        round_count=round_count,
                        answered_at=answered_at,
                    )
                else:
                    patch_values[path] = response
```

The existing `interaction_response_patcher` block stays for this task; Task 6 removes ChatBI usage after consumers are migrated.

- [ ] **Step 4: 交互节点返回标准路径 + 旧路径**

Modify `backend/apps/workflow/nodes/v1.py` imports:

```python
from apps.workflow.capabilities.interactions import standard_interaction_path
```

Inside `ChatBIV1InteractionNode.execute()`:

```python
            legacy_paths = spec.get("allowed_update_paths", [self._response_path])
            allowed_update_paths = [standard_interaction_path(request.node_name)]
            for path in legacy_paths:
                if path not in allowed_update_paths:
                    allowed_update_paths.append(path)
            interaction = {
                "prompt": spec.get("prompt"),
                "options": spec.get("options", []),
                "response_schema": spec.get("response_schema", {"type": "object"}),
                "allowed_update_paths": allowed_update_paths,
            }
```

- [ ] **Step 5: 更新 ChatBI flow 断言**

In `backend/tests/workflow/test_v1_flow.py`, add after a metric selection resume:

```python
    interaction_record = resumed.context.variables["interactions"]["ask_metric_selection"]
    assert interaction_record["response"] == {"metric": "sales_amount"}
    assert resumed.context.variables["metric_selection"] == {"metric": "sales_amount"}
```

- [ ] **Step 6: 运行目标测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_runtime_interactions.py tests/workflow/test_v1_flow.py::test_chatbi_v1_placeholder_metric_selection_can_resume_to_success -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/apps/workflow_engine/runtime/graph_runtime.py backend/apps/workflow/nodes/v1.py backend/tests/workflow_engine/test_runtime_interactions.py backend/tests/workflow/test_v1_flow.py
git commit -m "feat: persist scoped interaction responses"
```

---

### Task 3: 交互节点 metadata 与条件读取标准域

**Files:**
- Modify: `backend/apps/workflow/definitions/chatbi_v1.py`
- Modify: `backend/apps/workflow/conditions/core.py`
- Test: `backend/tests/workflow/test_interaction_conditions.py`
- Test: `backend/tests/workflow/test_v1_definition.py`

**Interfaces:**
- Consumes: `InteractionSpec`, `CHATBI_V1_INTERACTION_SPECS`, `read_interaction_response()`
- Produces:
  - interaction node metadata:
    `{"interaction": {"name": str, "legacy_path": str, "standard_path": str, "response_key": str, "max_rounds": int}}`
  - `InteractionResponseAnsweredCondition(node_name: str, legacy_key: str, label: str)`
  - `InteractionResponseSkippedCondition(node_name: str, legacy_key: str, label: str)`

- [ ] **Step 1: 写条件读取标准域失败测试**

Append to `backend/tests/workflow/test_interaction_conditions.py`:

```python
from apps.workflow.conditions.core import (
    InteractionResponseAnsweredCondition,
    InteractionResponseSkippedCondition,
)


def test_scoped_interaction_condition_prefers_standard_domain():
    context = WorkflowContext(
        variables={
            "interactions": {
                "ask_metric_selection": {
                    "node_name": "ask_metric_selection",
                    "round": 1,
                    "response": {"metric": 239},
                    "skipped": False,
                }
            },
            "metric_selection": {"skipped": True},
        }
    )
    result = NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)

    answered = InteractionResponseAnsweredCondition(
        node_name="ask_metric_selection",
        legacy_key="metric_selection",
        label="指标选择",
    ).evaluate(context, result)
    skipped = InteractionResponseSkippedCondition(
        node_name="ask_metric_selection",
        legacy_key="metric_selection",
        label="指标选择",
    ).evaluate(context, result)

    assert answered.matched is True
    assert skipped.matched is False
```

Append to `backend/tests/workflow/test_v1_definition.py`:

```python
def test_interaction_nodes_declare_standard_and_legacy_paths():
    definition = build_chatbi_v1_definition()

    metadata = definition.nodes["ask_metric_selection"].metadata["interaction"]

    assert metadata["standard_path"] == "variables.interactions.ask_metric_selection"
    assert metadata["legacy_path"] == "variables.metric_selection"
    assert metadata["response_key"] == "metric_selection"
    assert metadata["max_rounds"] == 2
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_interaction_conditions.py::test_scoped_interaction_condition_prefers_standard_domain tests/workflow/test_v1_definition.py::test_interaction_nodes_declare_standard_and_legacy_paths -q
```

Expected: FAIL，condition 构造参数或 metadata 不存在。

- [ ] **Step 3: 给交互节点添加 metadata**

Modify `backend/apps/workflow/definitions/chatbi_v1.py`:

```python
from apps.workflow.capabilities.interactions import CHATBI_V1_INTERACTION_SPECS


def _interaction_node(name: str, handler: str) -> NodeDefinition:
    spec = CHATBI_V1_INTERACTION_SPECS[name]
    return NodeDefinition(
        name=name,
        type=NodeType.INTERACTION,
        handler=handler,
        metadata={
            "interaction": {
                "name": spec.name,
                "response_key": spec.legacy_key,
                "standard_path": spec.standard_path,
                "legacy_path": spec.legacy_path,
                "max_rounds": spec.max_rounds,
            }
        },
    )
```

- [ ] **Step 4: 更新作用域条件读取标准域**

Modify `backend/apps/workflow/conditions/core.py`:

```python
from apps.workflow.capabilities.interactions import (
    CHATBI_V1_INTERACTION_SPECS,
    read_interaction_response,
)
```

Update scoped conditions:

```python
class InteractionResponseAnsweredCondition:
    def __init__(self, node_name: str, legacy_key: str, label: str) -> None:
        self._node_name = node_name
        self._legacy_key = legacy_key
        self._label = label

    def evaluate(self, context: WorkflowContext, result: NodeExecutionResult) -> ConditionDecision:
        value = read_interaction_response(context.variables, self._node_name, self._legacy_key)
        skipped = isinstance(value, dict) and value.get("skipped") is True
        matched = bool(value) and not skipped
        return ConditionDecision(
            matched=matched,
            reason_code="INTERACTION_ANSWERED" if matched else "INTERACTION_NOT_ANSWERED",
            reason_summary=f"用户已回答{self._label}" if matched else f"用户尚未回答{self._label}",
        )
```

Apply the same constructor fields to `InteractionResponseSkippedCondition`.

In `register_chatbi_conditions()` replace the hard-coded scoped map with:

```python
    labels = {
        "rewrite": "补充问题澄清",
        "intent": "分析方式澄清",
        "slot": "槽位澄清",
        "metric": "指标选择",
        "cross_model": "跨模型拆分确认",
    }
    for spec in CHATBI_V1_INTERACTION_SPECS.values():
        label = labels[spec.name]
        registry.register(
            f"interaction.{spec.name}.answered",
            InteractionResponseAnsweredCondition(spec.node_name, spec.legacy_key, label),
        )
        registry.register(
            f"interaction.{spec.name}.skipped",
            InteractionResponseSkippedCondition(spec.node_name, spec.legacy_key, label),
        )
```

Use `spec.max_rounds` when registering `ClarificationRoundGate`.

- [ ] **Step 5: 运行条件与定义测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_interaction_conditions.py tests/workflow/test_v1_definition.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/apps/workflow/definitions/chatbi_v1.py backend/apps/workflow/conditions/core.py backend/tests/workflow/test_interaction_conditions.py backend/tests/workflow/test_v1_definition.py
git commit -m "refactor: derive chatbi interaction routing metadata"
```

---

### Task 4: slot clarification 消费迁移到 knowledge adapter

**Files:**
- Modify: `backend/apps/workflow/capabilities/interactions.py`
- Modify: `backend/apps/workflow/capabilities/adapters/knowledge.py`
- Test: `backend/tests/workflow/test_headless_knowledge_adapter.py`
- Test: `backend/tests/workflow/test_v1_flow.py`

**Interfaces:**
- Produces:
  - `apply_slot_response_to_intent(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]`
- Consumes:
  - `ctx.slot_response`
  - existing `HeadlessKnowledgeAdapter._schema_scoped_by_subject_domain()`

- [ ] **Step 1: 写 slot response 纯函数失败测试**

Append to `backend/tests/workflow/test_headless_knowledge_adapter.py`:

```python
from apps.workflow.capabilities.interactions import apply_slot_response_to_intent


def test_apply_slot_response_to_intent_sets_dimension_filter_value():
    intent = {
        "ambiguous_slots": ["dimension"],
        "dimension_mentions": ["店铺"],
        "dimension_slots": [
            {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"}
        ],
    }

    updated = apply_slot_response_to_intent(
        intent,
        {"dimension_usage": "filter_value_required", "dimension_values": {"店铺": "店铺为1"}},
    )

    assert updated["ambiguous_slots"] == []
    assert updated["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}
    ]
    assert intent["dimension_slots"][0]["value"] is None
```

Add one subject domain case:

```python
def test_apply_slot_response_to_intent_sets_subject_domain():
    updated = apply_slot_response_to_intent(
        {"ambiguous_slots": ["subject_domain"]},
        {"domain_id": "7", "domain_name": "交易域"},
    )

    assert updated["subject_domain"]["domain_id"] == 7
    assert updated["subject_domain"]["domain_name"] == "交易域"
    assert updated["ambiguous_slots"] == []
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_headless_knowledge_adapter.py::test_apply_slot_response_to_intent_sets_dimension_filter_value tests/workflow/test_headless_knowledge_adapter.py::test_apply_slot_response_to_intent_sets_subject_domain -q
```

Expected: FAIL，提示 `apply_slot_response_to_intent` 不存在。

- [ ] **Step 3: 实现 slot response 纯函数**

Add to `backend/apps/workflow/capabilities/interactions.py`:

```python
from copy import deepcopy

from apps.workflow.capabilities.context import int_or_none


def apply_slot_response_to_intent(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """把槽位澄清回答应用到本地 intent 视图；不修改原始上下文。"""

    updated = deepcopy(intent) if isinstance(intent, dict) else {}
    if not updated or not response or response.get("skipped") is True:
        return updated
    ambiguous_slots = updated.get("ambiguous_slots") if isinstance(updated.get("ambiguous_slots"), list) else []
    if "subject_domain" in ambiguous_slots:
        return _apply_subject_domain_response(updated, response)
    return _apply_dimension_response(updated, response)
```

Implement helper functions by moving the current patcher logic:

```python
def _apply_subject_domain_response(intent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    domain_id = int_or_none(response.get("domain_id") or response.get("subject_domain_id"))
    if domain_id is None:
        return intent
    domain_name = response.get("subject_domain") or response.get("domain_name") or str(domain_id)
    intent["subject_domain"] = {
        "status": "selected",
        "domain_id": domain_id,
        "domain_name": str(domain_name),
        "domain_biz_name": response.get("domain_biz_name"),
        "confidence": 1.0,
        "reason": "用户已确认主题域",
        "candidate_domain_ids": [domain_id],
    }
    intent["ambiguous_slots"] = [
        slot for slot in intent.get("ambiguous_slots", []) if slot != "subject_domain"
    ]
    return intent
```

Move dimension normalization with Chinese comments preserved:

```python
def _normalize_dimension_value(dimension_name: str, raw_value: Any) -> str | None:
    if raw_value in (None, ""):
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    dimension = str(dimension_name or "").strip()
    for separator in ("为", "=", "是", ":", "："):
        prefix = f"{dimension}{separator}"
        if dimension and value.startswith(prefix):
            return value[len(prefix):].strip() or None
    return value
```

- [ ] **Step 4: 在 knowledge adapter 使用增强 intent 视图**

Modify `backend/apps/workflow/capabilities/adapters/knowledge.py`:

```python
from apps.workflow.capabilities.interactions import apply_slot_response_to_intent
```

In `retrieve()`:

```python
        intent = apply_slot_response_to_intent(ctx.intent, ctx.slot_response)
```

Use this local `intent` for subject domain scoping, candidate retrieval, rerank, missing slot detection and slot bindings. Do not assign it back to `ctx.variables`.

- [ ] **Step 5: 更新 v1 flow 断言：slot 回答不再由 runtime 改写原始 intent**

In the existing slot clarification resume test, assert:

```python
    assert outcome.context.variables["slot_response"]["dimension_values"] == {"店铺": "1"}
    assert outcome.context.variables["interactions"]["ask_slot_clarification"]["response"]["dimension_values"] == {"店铺": "1"}
    assert outcome.context.variables["intent"]["dimension_slots"][0]["value"] is None
```

Then assert the resulting knowledge/plan contains the filter:

```python
    plan_filters = outcome.context.variables["plan"]["filters"]
    assert any(item.get("value") == "1" for item in plan_filters)
```

- [ ] **Step 6: 运行 slot 相关测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_headless_knowledge_adapter.py tests/workflow/test_v1_flow.py::test_slot_clarification_patcher_uses_structured_dimension_values tests/workflow/test_v1_flow.py::test_slot_clarification_patcher_accepts_multiple_dimension_values -q
```

Expected: PASS after renaming the two patcher tests to interaction-consumption tests.

- [ ] **Step 7: 提交**

```bash
git add backend/apps/workflow/capabilities/interactions.py backend/apps/workflow/capabilities/adapters/knowledge.py backend/tests/workflow/test_headless_knowledge_adapter.py backend/tests/workflow/test_v1_flow.py
git commit -m "refactor: consume slot clarifications in knowledge retrieval"
```

---

### Task 5: metric selection 消费迁移到 QueryPlanBinder

**Files:**
- Modify: `backend/apps/workflow/capabilities/interactions.py`
- Modify: `backend/apps/workflow/capabilities/planning.py`
- Test: `backend/tests/workflow/test_query_plan_binding.py`
- Test: `backend/tests/workflow/test_v1_flow.py`

**Interfaces:**
- Produces:
  - `selected_metric_from_response(knowledge: dict[str, Any], response: dict[str, Any]) -> dict[str, Any] | None`
  - `prune_dimensions_for_selected_metric(selected_assets: dict[str, Any], slots: dict[str, Any], intent: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]`
- Consumes:
  - `ctx.metric_selection`
  - `QueryPlanBinder.bind()`

- [ ] **Step 1: 写 metric selection plan 失败测试**

Append to `backend/tests/workflow/test_query_plan_binding.py`:

```python
def test_binder_uses_metric_selection_response_without_mutating_knowledge():
    knowledge = {
        "hit": True,
        "status": "metric_ambiguous",
        "ambiguities": [
            {
                "type": "metric",
                "candidates": [
                    {"asset_id": 100, "biz_name": "visit_uv", "name": "访问人数"},
                    {"asset_id": 101, "biz_name": "order_cnt", "name": "订单数"},
                ],
            }
        ],
        "candidate_groups": {
            "metrics": [
                {"asset_id": 100, "biz_name": "visit_uv", "name": "访问人数"},
                {"asset_id": 101, "biz_name": "order_cnt", "name": "订单数"},
            ]
        },
        "selected_assets": {"metrics": [], "dimensions": [], "values": [], "terms": []},
        "slot_bindings": {"metrics": [], "group_dimensions": [], "time_filters": [], "value_filters": [], "dimension_filters": []},
    }
    variables = {
        "knowledge": knowledge,
        "intent": {"intent_type": "metric_query", "query_shape": {"select_mode": "aggregate"}},
        "interactions": {
            "ask_metric_selection": {
                "node_name": "ask_metric_selection",
                "round": 1,
                "response": {"metric": 100},
                "skipped": False,
            }
        },
    }

    plan = QueryPlanBinder().bind(_request(variables))

    assert plan["status"] == "ready"
    assert plan["strategy"] == "semantic_compiler"
    assert plan["metrics"] == [
        {"asset_type": "METRIC", "asset_id": 100, "display_name": "访问人数", "operator": None, "value": None}
    ]
    assert knowledge["selected_assets"]["metrics"] == []
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_query_plan_binding.py::test_binder_uses_metric_selection_response_without_mutating_knowledge -q
```

Expected: FAIL，当前 binder 未消费 `metric_selection`，计划不可用或 metrics 为空。

- [ ] **Step 3: 实现 metric selection helper**

Add to `backend/apps/workflow/capabilities/interactions.py`:

```python
def selected_metric_from_response(
    knowledge: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """从用户选择中定位指标候选；仅返回副本，不改写 knowledge。"""

    if not response or response.get("skipped") is True:
        return None
    selected = response.get("metric") or response.get("metric_id") or response.get("asset_id")
    if selected in (None, ""):
        return None
    selected_text = str(selected)
    for candidate in _metric_candidates(knowledge):
        if _candidate_matches(candidate, selected_text):
            return _metric_asset(candidate, selected)
    return _metric_asset(selected, selected)
```

Implement candidate helpers:

```python
def _metric_candidates(knowledge: dict[str, Any]) -> list[Any]:
    candidates: list[Any] = []
    for ambiguity in knowledge.get("ambiguities", []) or []:
        if isinstance(ambiguity, dict) and ambiguity.get("type") == "metric":
            candidates.extend(ambiguity.get("candidates") or [])
    groups = knowledge.get("candidate_groups")
    if isinstance(groups, dict):
        candidates.extend(groups.get("metrics") or [])
    return candidates
```

`_metric_asset()` must preserve `asset_id` as-is for placeholder string candidates and copy `model_id/payload` when candidate has them.

- [ ] **Step 4: 在 QueryPlanBinder 中优先使用用户选中指标**

Modify `backend/apps/workflow/capabilities/planning.py`:

```python
from apps.workflow.capabilities.interactions import selected_metric_from_response
```

In `QueryPlanBinder.bind()` after `slots = derive_semantic_slots(...)`:

```python
        selected_metric = selected_metric_from_response(knowledge, ctx.metric_selection)
        if selected_metric is not None:
            slots["metrics"] = [
                {
                    "asset_type": "METRIC",
                    "asset_id": selected_metric.get("asset_id"),
                    "display_name": selected_metric.get("display_name") or selected_metric.get("biz_name"),
                    "operator": None,
                    "value": None,
                }
            ]
```

Keep `group_bys` and `filters` derived from current knowledge/intention. If selected metric has `model_id`, filter group dimensions to the same model where model metadata exists.

- [ ] **Step 5: 更新 placeholder metric selection flow 断言**

In `test_chatbi_v1_placeholder_metric_selection_can_resume_to_success`, replace knowledge mutation assertions with:

```python
    assert resumed.context.variables["knowledge"]["status"] == "metric_ambiguous"
    assert resumed.context.variables["metric_selection"] == {"metric": "sales_amount"}
    assert resumed.context.variables["interactions"]["ask_metric_selection"]["response"] == {"metric": "sales_amount"}
    plan = resumed.context.variables["plan"]
    assert plan["status"] == "ready"
    assert plan["metrics"][0]["asset_id"] == "sales_amount"
```

- [ ] **Step 6: 运行 metric selection 测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_query_plan_binding.py tests/workflow/test_v1_flow.py::test_chatbi_v1_placeholder_metric_selection_can_resume_to_success -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/apps/workflow/capabilities/interactions.py backend/apps/workflow/capabilities/planning.py backend/tests/workflow/test_query_plan_binding.py backend/tests/workflow/test_v1_flow.py
git commit -m "refactor: bind metric selection in query plan"
```

---

### Task 6: 退役 ChatBIV1InteractionResponsePatcher

**Files:**
- Modify: `backend/apps/workflow/runtime.py`
- Modify: `backend/apps/workflow_engine/runtime/graph_runtime.py`
- Modify: `backend/tests/workflow/test_v1_flow.py`
- Modify: `backend/tests/workflow/test_v1_clarification_regressions.py`
- Modify: `backend/tests/workflow/test_v1_degradation.py`

**Interfaces:**
- Consumes: Task 4 and Task 5 node-level consumption.
- Produces: ChatBI v1 runtime constructed with `interaction_response_patcher=None`.

- [ ] **Step 1: 写 runtime 不再注入 patcher 的失败测试**

Append to `backend/tests/workflow/test_v1_flow.py`:

```python
def test_chatbi_v1_runtime_does_not_install_interaction_response_patcher():
    runtime = chatbi_runtime.build_placeholder_chatbi_v1_runtime(session=object())

    assert runtime._interaction_response_patcher is None
```

If direct private attribute access is considered too broad for this file, keep it here because existing tests already inspect runtime internals such as `_interactions`.

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_v1_flow.py::test_chatbi_v1_runtime_does_not_install_interaction_response_patcher -q
```

Expected: FAIL，runtime still has `ChatBIV1InteractionResponsePatcher`.

- [ ] **Step 3: 删除 ChatBIV1InteractionResponsePatcher 类与注入**

Modify `backend/apps/workflow/runtime.py`:

```python
# 删除 ChatBIV1InteractionResponsePatcher 类和不再使用的 deepcopy / ContextPatch / InteractionRequest / WorkflowRun import。
```

In `_build_chatbi_v1_runtime()`:

```python
        interaction_manager=DatabaseInteractionManager(session),
        node_execution_recorder=NodeExecutionRepository(session),
```

Do not pass `interaction_response_patcher`.

- [ ] **Step 4: 删除 GraphRuntime patcher 调用兼容口**

Modify `backend/apps/workflow_engine/runtime/graph_runtime.py` constructor only if no other workflow passes `interaction_response_patcher`:

```python
        interaction_response_patcher=None,
```

Remove the attribute and resume block:

```python
            if self._interaction_response_patcher is not None:
                extra_patch = self._interaction_response_patcher(run, answered, response)
                if extra_patch is not None:
                    run.context = self._context_patcher.apply(run.context, extra_patch)
```

If other tests still construct GraphRuntime with this argument, first update those tests to remove it, then remove the parameter. This keeps the engine free of business-level resume mutators.

- [ ] **Step 5: 更新旧 patcher 直接测试**

Delete assertions that instantiate `ChatBIV1InteractionResponsePatcher`. Replace them with Task 4/5 tests:

- slot clarification behavior asserted through `apply_slot_response_to_intent()` and v1 flow plan filters;
- metric selection behavior asserted through `QueryPlanBinder` and v1 flow plan metrics.

Search command:

```bash
rg -n "ChatBIV1InteractionResponsePatcher|interaction_response_patcher|patcher" backend/tests backend/apps
```

Expected after edits: no `ChatBIV1InteractionResponsePatcher`; no ChatBI runtime passes `interaction_response_patcher`.

- [ ] **Step 6: 运行 regression tests 确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_v1_flow.py tests/workflow/test_v1_clarification_regressions.py tests/workflow/test_v1_degradation.py tests/workflow_engine/test_runtime_interactions.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/apps/workflow/runtime.py backend/apps/workflow_engine/runtime/graph_runtime.py backend/tests/workflow/test_v1_flow.py backend/tests/workflow/test_v1_clarification_regressions.py backend/tests/workflow/test_v1_degradation.py backend/tests/workflow_engine/test_runtime_interactions.py
git commit -m "refactor: retire chatbi interaction response patcher"
```

---

### Task 7: Typos 修复、文档标记与最终验证

**Files:**
- Modify: `backend/apps/semantic/assets/quality_service.py`
- Modify: `docs/chatbi-v1-graph-refactor-analysis-and-design.md`
- Modify: `backend/apps/workflow/CHATBI_V1_FLOW_TEST_RECORD.md`
- Test: backend target test suites

**Interfaces:**
- Consumes: completed Tasks 1-6.
- Produces: Step 4 marked complete, CI spelling issue fixed.

- [ ] **Step 1: 修复 typos 检查命中的拼写**

Modify `backend/apps/semantic/assets/quality_service.py` around line 117:

```python
issues.append(
    _issue(
        "warning",
        "example.sql_unparsable",
        "SQL 示例无法识别为查询 SQL",
        "补充 SELECT 或 WITH 开头的可参考 SQL",
    )
)
```

Search to ensure no old token remains:

```bash
rg -n "sql_unparsable|unparsable" backend docs
```

Expected: no output.

- [ ] **Step 2: 更新 Step 4 文档状态**

Modify `docs/chatbi-v1-graph-refactor-analysis-and-design.md` Step 4 row to:

```markdown
| **Step 4** 交互子系统定稿 | `interactions` 域 + 条件工厂 + ResponsePatcher 退役；澄清轮次进节点 metadata。**（已完成：标准 `variables.interactions.<ask_node>` 域、旧 response 字段兼容、slot/metric 回答改由消费节点处理、ChatBI runtime 不再注入 ResponsePatcher；见 `test_interactions_domain.py`、`test_interaction_conditions.py`、`test_v1_flow.py`。）** | 五类澄清 + 跳过 + 多轮组合的端到端测试 |
```

Append to `backend/apps/workflow/CHATBI_V1_FLOW_TEST_RECORD.md` a short Step 4 note:

```markdown
## Step 4 交互子系统定稿记录

- 用户回答同时写入标准 `variables.interactions.<ask_node>` 域和旧 response 字段。
- ChatBI v1 runtime 不再使用 `ChatBIV1InteractionResponsePatcher`。
- 槽位澄清由 knowledge 节点构造本地 intent 视图消费；指标选择由 QueryPlanBinder 绑定为 plan。
- 作用域条件继续兼容旧 response 字段，保证历史 run 可读。
```

- [ ] **Step 3: 运行 Ruff**

Run:

```bash
cd backend
uv run ruff check apps/workflow apps/workflow_engine tests/workflow tests/workflow_engine
```

Expected: `All checks passed!`

- [ ] **Step 4: 运行目标后端测试**

Run:

```bash
cd backend
uv run pytest tests/workflow tests/workflow_engine tests/headless/test_semantic_sql_compiler.py tests/headless/test_sql_compiler_time_filters.py -q
```

Expected: all tests pass. If unrelated flaky or environment failure occurs, capture exact failure and inspect root cause before changing code.

- [ ] **Step 5: 运行前端目标检查**

Run:

```bash
cd frontend
npx eslint src/views/chat/execution-component/graphWorkflowDisplay.ts
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
```

Expected: both pass.

- [ ] **Step 6: 提交**

```bash
git add backend/apps/semantic/assets/quality_service.py docs/chatbi-v1-graph-refactor-analysis-and-design.md backend/apps/workflow/CHATBI_V1_FLOW_TEST_RECORD.md
git commit -m "docs: mark chatbi v1 step 4 complete"
```

- [ ] **Step 7: 推送当前分支**

Run:

```bash
git status -sb
git push -u ChatBI-NEW codex/headless-dataset-chat
```

Expected: local branch has no tracked changes before push; remote PR branch updates successfully.
