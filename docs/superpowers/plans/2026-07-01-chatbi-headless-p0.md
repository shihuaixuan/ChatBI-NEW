# ChatBI Semantic 单表查询 P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复首期五张单表模型的资产绑定、查询计划和 SQL 编译，使 20 个标准问题的接口结果与基线答案一致。

**Architecture:** 保留现有 ChatBI v1 工作流节点，在意图结果与 Semantic 检索结果之间增加严格的查询计划语义；候选资产先按用户提及和主模型裁剪，再交给 SQL 编译器。SQL 编译器扩展排序、限制、派生指标及单模型校验，当前数据集资产通过幂等脚本同步修正。

**Tech Stack:** Python 3.11、FastAPI、SQLModel、Pydantic、PyMySQL、pytest、现有 Semantic `SemanticSQLCompiler` 与 ChatBI Workflow Graph API。

---

## 文件结构

- 修改 `backend/apps/workflow/capabilities/adapters/question.py`：补齐绝对月份、业务谓词、多指标、排序和限制的结构化意图。
- 修改 `backend/apps/workflow/capabilities/adapters/knowledge.py`：按用户指标提及保留多指标，锁定主模型并按维度角色裁剪资产。
- 修改 `backend/apps/workflow/capabilities/adapters/interaction.py`：指标交互支持一次选择多个指标。
- 修改 `backend/apps/workflow/capabilities/adapters/sql.py`：把查询计划中的排序、限制和严格槽位传递给编译器。
- 修改 `backend/apps/semantic/sql_compiler.py`：支持排序、派生表达式、单模型约束和编译后语义校验。
- 创建 `backend/scripts/configure_chatbi_first_phase_assets.py`：幂等修正数据集 243 的首期 Semantic 资产。
- 修改 `backend/scripts/evaluate_chatbi_first_phase.py`：保留回归基线并输出修复前后差异。
- 修改对应测试文件：覆盖新增行为。

### Task 1: 完整 QueryPlan 意图

**Files:**
- Modify: `backend/apps/workflow/capabilities/adapters/question.py`
- Test: `backend/tests/workflow/test_question_understanding_flow.py`
- Test: `backend/tests/workflow/test_question_rewrite_and_answer_adapters.py`
- Test: `backend/tests/workflow/test_time_slots.py`

- [ ] **Step 1: 写绝对月份、多指标和 TopN 的失败测试**

```python
def test_understand_builds_complete_plan_for_monthly_topn_query():
    result = adapter.understand(
        request_for("2026 年 6 月销售 GMV 最高的 5 个档口是哪些？")
    )

    assert result["metric_mentions"] == ["销售 GMV"]
    assert result["dimension_slots"] == [
        {
            "name": "档口",
            "role": "group_by",
            "value": None,
            "value_status": "not_provided",
        }
    ]
    assert result["time_range"]["normalized"] == {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }
    assert result["query_shape"]["order_by"] == [
        {"field": "销售 GMV", "direction": "desc"}
    ]
    assert result["query_shape"]["limit"] == 5


def test_understand_keeps_all_explicit_metrics():
    result = adapter.understand(
        request_for("最近 30 天每天的总订单数和总 GMV 趋势如何？")
    )

    assert result["metric_mentions"] == ["总订单数", "总 GMV"]
```

- [ ] **Step 2: 运行测试并确认因缺少结构化字段而失败**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_question_understanding_flow.py \
  tests/workflow/test_question_rewrite_and_answer_adapters.py \
  tests/workflow/test_time_slots.py -q
```

Expected: 新增断言失败，现有测试保持通过。

- [ ] **Step 3: 实现最小意图结构化逻辑**

在规则回退和大模型结果标准化的共同出口补齐以下结构：

```python
# 绝对月份统一转换成左闭右开的时间范围。
time_range = {
    "raw": "2026 年 6 月",
    "value_status": "provided",
    "normalized": {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    },
}

# 排名语义必须同时携带方向和数量。
query_shape["order_by"] = [{"field": metric_name, "direction": direction}]
query_shape["limit"] = limit
```

同时将以下业务词写入 `filter_mentions`：

```python
BUSINESS_FILTERS = {
    "负库存": {"name": "库存数量", "operator": "<", "value": 0},
    "超时未发": {"name": "是否超时", "operator": "=", "value": 1},
    "30 天未动销": {"name": "未动销天数", "operator": ">=", "value": 30},
    "逾期": {"name": "逾期金额", "operator": ">", "value": 0},
}
```

- [ ] **Step 4: 运行意图测试并确认通过**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_question_understanding_flow.py \
  tests/workflow/test_question_rewrite_and_answer_adapters.py \
  tests/workflow/test_time_slots.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/workflow/capabilities/adapters/question.py \
  backend/tests/workflow/test_question_understanding_flow.py \
  backend/tests/workflow/test_question_rewrite_and_answer_adapters.py \
  backend/tests/workflow/test_time_slots.py
git commit -m "feat: complete chatbi query plan intent"
```

### Task 2: 多指标门控与主模型锁定

**Files:**
- Modify: `backend/apps/workflow/capabilities/adapters/knowledge.py`
- Test: `backend/tests/workflow/test_headless_knowledge_adapter.py`
- Test: `backend/tests/workflow/test_headless_domain_routing.py`

- [ ] **Step 1: 写多指标和模型隔离的失败测试**

```python
def test_candidate_gate_keeps_all_explicit_metric_mentions():
    result = adapter.retrieve(
        request_with_intent(
            "总订单数和总GMV",
            metric_mentions=["总订单数", "总GMV"],
        )
    )

    assert [item["biz_name"] for item in result["selected_assets"]["metrics"]] == [
        "order_cnt_total",
        "gmv_total",
    ]


def test_retrieve_drops_dimensions_outside_locked_metric_model():
    result = adapter.retrieve(request_with_intent("各档口总GMV", metric_mentions=["总GMV"]))

    assert {item["model_id"] for item in result["selected_assets"]["metrics"]} == {10}
    assert {item["model_id"] for item in result["selected_assets"]["dimensions"]} == {10}
    assert [item["biz_name"] for item in result["selected_assets"]["dimensions"]] == ["stall_id"]
```

- [ ] **Step 2: 运行测试并确认 CandidateGate 只保留 Top1**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_headless_knowledge_adapter.py \
  tests/workflow/test_headless_domain_routing.py -q
```

Expected: 多指标断言得到一个指标；模型隔离测试包含无关维度。

- [ ] **Step 3: 实现显式提及驱动的多指标门控**

调整 `CandidateGate.decide` 接收期望指标数量或显式指标标识：

```python
def decide(
    self,
    candidate_groups: dict[str, list[dict[str, Any]]],
    *,
    expected_metric_mentions: list[str] | None = None,
) -> dict[str, Any]:
    selected_metrics = self._select_explicit_metrics(
        metrics,
        expected_metric_mentions or [],
    )
```

选择规则：

- 每个显式指标提及最多绑定一个最高分候选；
- 同一资产去重；
- 未显式要求多指标时保留现有单指标歧义策略；
- 主模型由已选指标模型的交集确定；
- 主模型确定后过滤其他模型的维度、值和术语。

- [ ] **Step 4: 按意图角色裁剪维度**

```python
requested_dimension_names = {
    str(slot.get("name") or "")
    for slot in intent.get("dimension_slots") or []
    if slot.get("role") in {"group_by", "display", "filter"}
}
```

只保留明确角色维度以及显式时间过滤所需的默认时间维度。不得因为字段在候选列表中就自动进入 `selected_assets`。

- [ ] **Step 5: 运行知识检索测试并确认通过**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_headless_knowledge_adapter.py \
  tests/workflow/test_headless_domain_routing.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交**

```bash
git add backend/apps/workflow/capabilities/adapters/knowledge.py \
  backend/tests/workflow/test_headless_knowledge_adapter.py \
  backend/tests/workflow/test_headless_domain_routing.py
git commit -m "feat: lock headless assets to query model"
```

### Task 3: 多指标用户交互

**Files:**
- Modify: `backend/apps/workflow/capabilities/adapters/interaction.py`
- Modify: `backend/apps/workflow/runtime.py`
- Test: `backend/tests/workflow/test_interaction_adapter.py`
- Test: `backend/tests/workflow_engine/test_runtime_interactions.py`

- [ ] **Step 1: 写多选交互失败测试**

```python
def test_metric_selection_accepts_multiple_metrics():
    result = adapter.ask_metric_selection(
        interaction_request(
            response={"metrics": ["265", "271"]},
            candidates=[metric(265, "总订单数"), metric(271, "总GMV")],
        )
    )

    assert result["selected_metric_ids"] == [265, 271]
```

- [ ] **Step 2: 运行测试并确认当前交互只接受 `metric`**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_interaction_adapter.py \
  tests/workflow_engine/test_runtime_interactions.py -q
```

Expected: 新测试失败，错误表现为只保留一个指标或响应格式不识别。

- [ ] **Step 3: 实现向后兼容的多选协议**

```python
raw_ids = response.get("metrics")
if raw_ids is None and response.get("metric") is not None:
    raw_ids = [response["metric"]]

# 对指标 ID 转整数、去重，并验证它们均来自当前候选集合。
selected_metric_ids = _validated_metric_ids(raw_ids, candidates)
```

运行时把多个已选指标合并进知识变量，旧格式 `{"metric": "265"}` 继续可用。

- [ ] **Step 4: 运行交互测试并确认通过**

Run:

```bash
cd backend
uv run pytest tests/workflow/test_interaction_adapter.py \
  tests/workflow_engine/test_runtime_interactions.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/workflow/capabilities/adapters/interaction.py \
  backend/apps/workflow/runtime.py \
  backend/tests/workflow/test_interaction_adapter.py \
  backend/tests/workflow_engine/test_runtime_interactions.py
git commit -m "feat: support multi metric interaction"
```

### Task 4: 确定性 SQL 编译

**Files:**
- Modify: `backend/apps/semantic/sql_compiler.py`
- Modify: `backend/apps/workflow/capabilities/adapters/sql.py`
- Test: `backend/tests/semantic/test_semantic_sql_compiler.py`
- Test: `backend/tests/semantic/test_sql_compiler_time_filters.py`
- Test: `backend/tests/workflow/test_sql_adapter.py`

- [ ] **Step 1: 写排序、TopN、派生指标和单模型校验的失败测试**

```python
def test_compiler_renders_derived_metric_order_and_limit():
    result = compiler.compile(
        SemanticSQLCompileRequest(
            schema=stall_schema(),
            slots={
                "metrics": [{"asset_type": "METRIC", "asset_id": 275}],
                "dimensions": [{"asset_type": "DIMENSION", "asset_id": 301}],
                "order_by": [
                    {"asset_type": "METRIC", "asset_id": 275, "direction": "desc"}
                ],
            },
            limit=5,
        )
    )

    assert "sum(stall_order.gmv_sale) / nullif(sum(stall_order.order_cnt_sale), 0) as aov_sale" in result.sql
    assert "group by stall_order.stall_id" in result.sql
    assert "order by aov_sale desc limit 5" in result.sql


def test_compiler_rejects_assets_from_multiple_models_for_single_table_plan():
    with pytest.raises(ValueError, match="SEMANTIC_SQL_SINGLE_MODEL_REQUIRED"):
        compiler.compile(mixed_model_request())
```

- [ ] **Step 2: 运行测试并确认缺少排序和派生公式**

Run:

```bash
cd backend
uv run pytest tests/semantic/test_semantic_sql_compiler.py \
  tests/semantic/test_sql_compiler_time_filters.py \
  tests/workflow/test_sql_adapter.py -q
```

Expected: 新测试失败；现有测试保持通过。

- [ ] **Step 3: 扩展编译请求和 SQL 生成**

```python
@dataclass
class SemanticSQLCompileRequest:
    schema: DataSetSchema
    question: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    metric_ids: list[int] = field(default_factory=list)
    dimension_ids: list[int] = field(default_factory=list)
    repair_context: dict[str, Any] = field(default_factory=dict)
    order_by: list[dict[str, Any]] = field(default_factory=list)
    limit: int | None = None
    single_model: bool = False
```

编译顺序固定为 `SELECT -> FROM -> WHERE -> GROUP BY -> ORDER BY -> LIMIT`。当 `single_model=True` 且资产跨模型时抛出 `SEMANTIC_SQL_SINGLE_MODEL_REQUIRED`。

- [ ] **Step 4: 正确编译派生指标**

优先读取指标资产中的完整表达式；对表达式里的字段引用按主模型别名限定。禁止在派生指标上再次套用默认聚合函数。

```python
if metric_define_type == "FIELD":
    expression = metric_params["expr"]
    return self._qualify_metric_expression(expression, model_name), self._contains_aggregate(expression)
```

- [ ] **Step 5: SqlAdapter 只传递 QueryPlan 明确字段**

`_compile_slots` 不再合并所有 `selected_assets.dimensions`，而是以 `slot_bindings.dimensions` 为准；只有兼容旧调用且不存在槽位绑定时才回退到候选资产。将 `query_shape.order_by` 和 `query_shape.limit` 映射到编译请求。

- [ ] **Step 6: 运行 SQL 测试并确认通过**

Run:

```bash
cd backend
uv run pytest tests/semantic/test_semantic_sql_compiler.py \
  tests/semantic/test_sql_compiler_time_filters.py \
  tests/workflow/test_sql_adapter.py -q
```

Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add backend/apps/semantic/sql_compiler.py \
  backend/apps/workflow/capabilities/adapters/sql.py \
  backend/tests/semantic/test_semantic_sql_compiler.py \
  backend/tests/semantic/test_sql_compiler_time_filters.py \
  backend/tests/workflow/test_sql_adapter.py
git commit -m "feat: compile deterministic single model sql"
```

### Task 5: 首期 Semantic 资产幂等修正

**Files:**
- Create: `backend/scripts/configure_chatbi_first_phase_assets.py`
- Test: `backend/tests/semantic/test_chatbi_first_phase_asset_config.py`

- [ ] **Step 1: 写资产配置计划的失败测试**

```python
def test_asset_plan_uses_one_default_business_time_per_model():
    plan = build_asset_plan()

    assert plan["fct_stall_order_daily"]["default_time"] == "stat_date"
    assert plan["snap_unshipped_order"]["default_time"] == "snapshot_date"
    assert plan["snap_product_inventory"]["default_time"] == "snapshot_date"
    assert plan["fct_customer_trade_daily"]["default_time"] == "stat_date"
    assert plan["snap_customer_arrears"]["default_time"] == "snapshot_date"


def test_asset_plan_contains_required_business_metrics():
    plan = build_asset_plan()
    metrics = {
        metric["biz_name"]: metric
        for model in plan.values()
        for metric in model["metrics"]
    }

    assert metrics["aov_sale"]["expr"] == (
        "SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0)"
    )
    assert metrics["unshipped_order_cnt"]["expr"] == "COUNT(DISTINCT order_id)"
    assert metrics["overdue_customer_cnt"]["expr"] == (
        "COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END)"
    )
```

- [ ] **Step 2: 运行测试并确认配置模块不存在**

Run:

```bash
cd backend
uv run pytest tests/semantic/test_chatbi_first_phase_asset_config.py -q
```

Expected: FAIL，模块无法导入。

- [ ] **Step 3: 实现资产计划和幂等更新**

脚本参数：

```python
parser.add_argument("--dataset-id", type=int, default=243)
parser.add_argument("--oid", type=int, default=1)
parser.add_argument("--dry-run", action="store_true")
```

实现要求：

- 以模型 `table_query` 和指标 `biz_name` 为稳定标识；
- 每个模型只保留一个默认时间维度；
- `created_at`、`updated_at` 的 `is_default_time` 设置为 `False`；
- 已有指标更新公式和别名，缺失指标创建；
- 重复运行不新增重复资产；
- 输出新增、更新和未变化数量；
- 所有代码注释使用中文。

- [ ] **Step 4: 运行资产配置测试并确认通过**

Run:

```bash
cd backend
uv run pytest tests/semantic/test_chatbi_first_phase_asset_config.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 先预览再更新当前资产**

Run:

```bash
cd backend
uv run python scripts/configure_chatbi_first_phase_assets.py \
  --dataset-id 243 --oid 1 --dry-run
uv run python scripts/configure_chatbi_first_phase_assets.py \
  --dataset-id 243 --oid 1
uv run python scripts/configure_chatbi_first_phase_assets.py \
  --dataset-id 243 --oid 1 --dry-run
```

Expected: 第一次预览列出变化；执行成功；第二次预览显示无待修改项。

- [ ] **Step 6: 提交**

```bash
git add backend/scripts/configure_chatbi_first_phase_assets.py \
  backend/tests/semantic/test_chatbi_first_phase_asset_config.py
git commit -m "feat: configure first phase headless assets"
```

### Task 6: 模块回归和 20 问接口验收

**Files:**
- Modify: `backend/scripts/evaluate_chatbi_first_phase.py`
- Modify: `docs/tech/chatbi_first_phase_20q_evaluation.md`
- Modify: `docs/tech/chatbi_first_phase_20q_evaluation.json`

- [ ] **Step 1: 运行相关模块测试**

Run:

```bash
cd backend
uv run pytest tests/workflow tests/semantic -q
```

Expected: 全部通过，无新增失败。

- [ ] **Step 2: 执行静态检查**

Run:

```bash
cd backend
uv run ruff check \
  apps/workflow/capabilities/adapters/question.py \
  apps/workflow/capabilities/adapters/knowledge.py \
  apps/workflow/capabilities/adapters/interaction.py \
  apps/workflow/capabilities/adapters/sql.py \
  apps/semantic/sql_compiler.py \
  scripts/configure_chatbi_first_phase_assets.py \
  scripts/evaluate_chatbi_first_phase.py
```

Expected: `All checks passed!`

- [ ] **Step 3: 确认服务使用最新代码**

重启当前后端服务，确认 `GET /api/health` 或现有健康检查端点成功；不得启动第二个占用同一端口的进程。

- [ ] **Step 4: 调用真实 Graph API 执行 20 问**

Run:

```bash
cd backend
uv run python scripts/evaluate_chatbi_first_phase.py
```

Expected:

```text
完成: 20/20
API 成功: 20/20
结果一致: 20/20
```

- [ ] **Step 5: 对失败项继续执行红绿重构循环**

任何未通过问题都必须定位到以下一个明确层级：

- 意图；
- 主模型；
- 指标；
- 维度；
- 过滤；
- SQL；
- 结果。

为该层级先添加最小失败测试，再修复，直到 20/20；不得通过修改标准答案使错误结果通过。

- [ ] **Step 6: 完成最终验证**

Run:

```bash
cd backend
uv run pytest tests/workflow tests/semantic -q
uv run ruff check apps/workflow apps/semantic scripts/configure_chatbi_first_phase_assets.py scripts/evaluate_chatbi_first_phase.py
git diff --check
```

Expected: 测试、静态检查和 diff 检查全部通过。

- [ ] **Step 7: 提交回归脚本和报告**

```bash
git add backend/scripts/evaluate_chatbi_first_phase.py \
  docs/tech/chatbi_first_phase_20q_evaluation.md \
  docs/tech/chatbi_first_phase_20q_evaluation.json
git commit -m "test: verify chatbi first phase questions"
```
