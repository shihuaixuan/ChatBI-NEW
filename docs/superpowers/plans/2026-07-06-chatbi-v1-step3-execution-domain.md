# ChatBI v1 Step 3 Execution Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 ChatBI v1 单查询与拆分查询的执行结果结构，并行执行拆分查询，把完整结果持久化为 artifact，同时让答案模型只读取精简投影视图。

**Architecture:** `variables.execution` 成为标准执行域，`variables.sql_execution` 在迁移期镜像同一输出。SQL adapter 通过线程安全执行网关运行查询，通过文件 artifact store 保存完整结果；上下文只保存 `queries[]/results[]`、统计、引用和少量样本。旧条件、Trace、前端与推荐逻辑改为标准域优先、旧域回退。

**Tech Stack:** Python 3.11、Pydantic v2、SQLModel/SQLAlchemy、pytest、`concurrent.futures.ThreadPoolExecutor`、Vue 3/TypeScript、Node.js 内置 test runner。

---

## 文件职责

- Create `backend/apps/workflow_engine/infrastructure/artifacts/__init__.py`：导出 artifact 正文存储实现。
- Create `backend/apps/workflow_engine/infrastructure/artifacts/file_store.py`：文件正文原子写入、摘要校验及元数据持久化。
- Modify `backend/apps/workflow_engine/infrastructure/persistence/artifact_repository.py`：补齐按 ID 读取元数据。
- Create `backend/tests/workflow_engine/test_file_artifact_store.py`：artifact 文件与元数据契约测试。
- Create `backend/apps/chatbi_workflow/capabilities/execution.py`：统一 query/result 构造、执行聚合和线程安全 SQL 执行网关。
- Modify `backend/apps/chatbi_workflow/capabilities/adapters/sql.py`：单/拆分统一执行、artifact 写入与并行调度。
- Modify `backend/apps/chatbi_workflow/schemas/v1.py`：标准执行域 Pydantic 契约。
- Modify `backend/apps/chatbi_workflow/nodes/v1.py`：支持能力输出镜像到兼容路径，并传递 `run_id`。
- Modify `backend/apps/chatbi_workflow/definitions/chatbi_v1.py`：执行节点主写 `variables.execution`，镜像 `variables.sql_execution`。
- Modify `backend/apps/chatbi_workflow/capabilities/context.py`：标准执行域优先读取。
- Modify `backend/apps/chatbi_workflow/conditions/core.py`：执行路由读取标准域并兼容旧域。
- Modify `backend/apps/chatbi_workflow/capabilities/adapters/recommendation.py`：读取统一执行域。
- Modify `backend/apps/chatbi_workflow/runtime.py`：注入线程安全执行网关与文件 artifact store。
- Modify `backend/apps/chatbi_workflow/capabilities/adapters/answer.py`：构造并使用答案投影视图。
- Modify `backend/apps/workflow_engine/api/service.py`：Trace 统一投影单/拆分执行摘要。
- Modify `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts`：前端优先消费 `results[]`。
- Modify/add corresponding tests under `backend/tests/chatbi_workflow`、`backend/tests/workflow_engine` and `frontend/src/views/chat/execution-component`.

### Task 1: 文件 Artifact Store

**Files:**
- Create: `backend/apps/workflow_engine/infrastructure/artifacts/__init__.py`
- Create: `backend/apps/workflow_engine/infrastructure/artifacts/file_store.py`
- Modify: `backend/apps/workflow_engine/infrastructure/persistence/artifact_repository.py`
- Test: `backend/tests/workflow_engine/test_file_artifact_store.py`
- Modify: `.gitignore`

- [ ] **Step 1: 写 artifact 持久化失败测试**

```python
def test_file_artifact_store_persists_json_body_and_metadata(tmp_path, session):
    store = FileArtifactStore(root=tmp_path, session_factory=lambda: session)
    ref = store.put_json(
        run_id="run-1",
        kind="sql_result",
        payload={"query_id": "query-0", "rows": [{"value": 1}], "row_count": 1},
        metadata={"query_id": "query-0", "row_count": 1},
    )

    artifact, content = store.get(ref.artifact_id)
    assert json.loads(content) == {
        "query_id": "query-0",
        "row_count": 1,
        "rows": [{"value": 1}],
    }
    assert artifact.digest == ref.digest
    assert artifact.size == len(content)
    assert artifact.storage_uri.startswith("file://")
```

同时增加路径逃逸、摘要一致性和元数据写入失败后文件清理测试。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_file_artifact_store.py -q
```

Expected: collection 失败，提示 `FileArtifactStore` 尚不存在。

- [ ] **Step 3: 实现最小文件存储**

```python
class FileArtifactStore:
    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"artifact-{uuid4().hex}"
        final_path = self._root / f"{artifact_id}.json"
        temporary_path = self._root / f".{artifact_id}.tmp"
        temporary_path.write_bytes(content)
        temporary_path.replace(final_path)
        artifact = WorkflowArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=kind,
            content_type="application/json",
            size=len(content),
            digest=f"sha256:{digest}",
            metadata=metadata or {},
            storage_uri=final_path.resolve().as_uri(),
            created_at=datetime.now(timezone.utc),
        )
        self._persist_metadata(artifact, final_path)
        return ArtifactRef.model_validate(artifact.model_dump())
```

`ArtifactRepository.get()` 必须返回 `WorkflowArtifact`；默认目录 `backend/data/workflow_artifacts/` 加入 `.gitignore`。

- [ ] **Step 4: 运行 artifact 测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_file_artifact_store.py tests/workflow_engine/test_persistence_models.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add .gitignore backend/apps/workflow_engine/infrastructure/artifacts backend/apps/workflow_engine/infrastructure/persistence/artifact_repository.py backend/tests/workflow_engine/test_file_artifact_store.py
git commit -m "feat: persist workflow result artifacts"
```

### Task 2: 标准执行域模型

**Files:**
- Create: `backend/apps/chatbi_workflow/capabilities/execution.py`
- Modify: `backend/apps/chatbi_workflow/schemas/v1.py`
- Test: `backend/tests/chatbi_workflow/test_execution_domain.py`

- [ ] **Step 1: 写 query/result 同构与聚合失败测试**

```python
def test_build_execution_output_uses_same_shape_for_single_and_split_queries():
    query = ExecutionQuery(query_id="query-0", sql="select 1", datasource_id=1)
    result = ExecutionResult(
        query_id="query-0",
        status="succeeded",
        row_count=1,
        fields=["value"],
        sample_rows=[{"value": 1}],
        sampled_row_count=1,
        result_truncated=False,
        execution_ms=3,
    )

    output = build_execution_output([query], [result])

    assert output["status"] == "succeeded"
    assert output["queries"][0]["query_id"] == output["results"][0]["query_id"]
    assert output["rows"] == [{"value": 1}]
```

再覆盖多查询 `rows=[]`、聚合行数、首个错误透传和输入 query/result 不对齐时显式失败。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_execution_domain.py -q
```

Expected: import 失败，提示执行域类型尚不存在。

- [ ] **Step 3: 实现执行域类型和纯聚合函数**

```python
class ExecutionQuery(BaseModel):
    query_id: str
    sql: str
    datasource_id: int
    plan_ref: int | None = None
    model_id: int | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    query_id: str
    status: Literal["succeeded", "failed"]
    row_count: int = 0
    fields: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    sampled_row_count: int = 0
    result_truncated: bool = False
    artifact_ref: ArtifactRef | None = None
    execution_ms: int = 0
    error_code: str | None = None
    message: str | None = None
```

`build_execution_output()` 必须校验 query ID 一一对应，并生成迁移期顶层摘要。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_execution_domain.py tests/chatbi_workflow/test_v1_schemas.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/chatbi_workflow/capabilities/execution.py backend/apps/chatbi_workflow/schemas/v1.py backend/tests/chatbi_workflow/test_execution_domain.py
git commit -m "feat: define unified chatbi execution domain"
```

### Task 3: 单查询执行与 Artifact 接入

**Files:**
- Modify: `backend/apps/chatbi_workflow/capabilities/adapters/sql.py`
- Modify: `backend/apps/chatbi_workflow/capabilities/context.py`
- Test: `backend/tests/chatbi_workflow/test_sql_adapter.py`

- [ ] **Step 1: 写单查询统一输出失败测试**

```python
def test_sql_adapter_execute_returns_query_result_and_artifact_ref():
    artifact_store = FakeArtifactStore()
    adapter = SqlAdapter(
        execute_tool=FakeExecuteTool(rows=[{"value": 1}, {"value": 2}]),
        artifact_store=artifact_store,
        sample_row_limit=1,
    )

    output = adapter.execute(_execution_request(run_id="run-1"))

    assert output["queries"] == [
        {"query_id": "query-0", "sql": "select value from t", "datasource_id": 5,
         "plan_ref": 0, "model_id": None, "metrics": [], "dimensions": []}
    ]
    assert output["results"][0]["sample_rows"] == [{"value": 1}]
    assert output["results"][0]["row_count"] == 2
    assert output["results"][0]["artifact_ref"]["artifact_id"] == "artifact-1"
    assert artifact_store.payload["rows"] == [{"value": 1}, {"value": 2}]
```

再覆盖 artifact 写入失败转换为 `SQL_RESULT_ARTIFACT_WRITE_FAILED`。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_sql_adapter.py -q
```

Expected: 新断言失败，因为当前输出没有 `queries/results` 且没有真实 artifact 写入。

- [ ] **Step 3: 实现单查询标准执行**

`ChatBIRunContext` 增加 `run_id`，节点请求将 run ID 传给 gateway。`SqlAdapter._execute_query()` 完成权限、执行、artifact 与 `ExecutionResult` 构造：

```python
artifact_ref = self._artifact_store.put_json(
    run_id=ctx.run_id,
    kind="sql_result",
    payload={
        "query_id": query.query_id,
        "fields": fields,
        "rows": rows,
        "row_count": row_count,
    },
    metadata={"query_id": query.query_id, "row_count": row_count},
)
```

执行失败时返回失败 result，不写 artifact。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_sql_adapter.py tests/chatbi_workflow/test_execution_domain.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/chatbi_workflow/capabilities/adapters/sql.py backend/apps/chatbi_workflow/capabilities/context.py backend/tests/chatbi_workflow/test_sql_adapter.py
git commit -m "feat: store single query results as artifacts"
```

### Task 4: 拆分查询并行执行

**Files:**
- Modify: `backend/apps/chatbi_workflow/capabilities/execution.py`
- Modify: `backend/apps/chatbi_workflow/capabilities/adapters/sql.py`
- Modify: `backend/apps/chatbi_workflow/runtime.py`
- Test: `backend/tests/chatbi_workflow/test_sql_adapter.py`

- [ ] **Step 1: 写并行和部分失败测试**

```python
def test_split_queries_execute_in_parallel_and_keep_query_order():
    barrier = threading.Barrier(2)
    gateway = BlockingExecutionGateway(barrier)
    adapter = SqlAdapter(execution_gateway=gateway, artifact_store=FakeArtifactStore())

    output = adapter.execute_split(_split_execution_request())

    assert [item["query_id"] for item in output["results"]] == ["query-0", "query-1"]
    assert gateway.max_active_calls == 2


def test_split_query_failure_preserves_successful_sibling_result():
    gateway = SelectiveFailureGateway(failed_sql="select failed")
    adapter = SqlAdapter(execution_gateway=gateway, artifact_store=FakeArtifactStore())

    output = adapter.execute_split(_split_execution_request())

    assert output["status"] == "failed"
    assert [item["status"] for item in output["results"]] == ["succeeded", "failed"]
    assert output["error_code"] == "sql_execute_error"
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_sql_adapter.py -q
```

Expected: 并行断言失败，当前实现串行且失败时提前返回。

- [ ] **Step 3: 实现有界并行执行**

```python
with ThreadPoolExecutor(max_workers=min(len(queries), self._max_parallel_queries)) as executor:
    futures = [
        executor.submit(self._execute_query, ctx, query)
        for query in queries
    ]
    results = [self._future_result(query, future) for query, future in zip(queries, futures)]
```

运行时注入 `SessionSqlExecutionGateway`，其每次调用使用独立 Session：

```python
class SessionSqlExecutionGateway:
    def run(self, payload: dict[str, Any]) -> ToolResult:
        with self._session_factory() as session:
            return SqlExecuteTool(session).run(payload)
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_sql_adapter.py tests/chatbi_workflow/test_v1_flow.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/chatbi_workflow/capabilities/execution.py backend/apps/chatbi_workflow/capabilities/adapters/sql.py backend/apps/chatbi_workflow/runtime.py backend/tests/chatbi_workflow/test_sql_adapter.py
git commit -m "feat: execute split chatbi queries in parallel"
```

### Task 5: 执行域双写与旧消费者兼容

**Files:**
- Modify: `backend/apps/chatbi_workflow/nodes/v1.py`
- Modify: `backend/apps/chatbi_workflow/definitions/chatbi_v1.py`
- Modify: `backend/apps/chatbi_workflow/capabilities/context.py`
- Modify: `backend/apps/chatbi_workflow/conditions/core.py`
- Modify: `backend/apps/chatbi_workflow/capabilities/adapters/recommendation.py`
- Modify: `backend/apps/chatbi_workflow/capabilities/adapters/sql.py`
- Test: `backend/tests/chatbi_workflow/test_v1_flow.py`
- Test: `backend/tests/chatbi_workflow/test_conditions.py`
- Test: `backend/tests/chatbi_workflow/test_run_context.py`

- [ ] **Step 1: 写双写与标准域优先测试**

```python
def test_execution_node_writes_standard_and_compatibility_domains():
    run = _run_v1_main_path()
    assert run.context.variables["execution"] == run.context.variables["sql_execution"]


def test_run_context_prefers_standard_execution_domain():
    ctx = ChatBIRunContext({
        "variables": {
            "execution": {"status": "succeeded", "row_count": 2},
            "sql_execution": {"status": "failed", "row_count": 0},
        }
    })
    assert ctx.execution["status"] == "succeeded"
```

条件测试必须证明只有 `execution` 时成功/失败路由仍正确。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_v1_flow.py tests/chatbi_workflow/test_conditions.py tests/chatbi_workflow/test_run_context.py -q
```

Expected: 缺少 `variables.execution` 或条件仍只读旧域。

- [ ] **Step 3: 实现镜像输出和兼容读取**

`ChatBIV1CapabilityNode` 接受镜像路径：

```python
set_values = {self._output_path: result}
set_values.update({path: result for path in self._mirror_output_paths})
return NodeExecutionResult(
    status=NodeResultStatus.SUCCEEDED,
    patch=ContextPatch(set_values=set_values),
)
```

执行 handler 主路径设为 `variables.execution`，镜像路径为 `variables.sql_execution`。条件层使用一个 `_execution(context)` helper，优先标准域。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_v1_flow.py tests/chatbi_workflow/test_conditions.py tests/chatbi_workflow/test_run_context.py tests/chatbi_workflow/test_v1_definition.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/chatbi_workflow/nodes/v1.py backend/apps/chatbi_workflow/definitions/chatbi_v1.py backend/apps/chatbi_workflow/capabilities/context.py backend/apps/chatbi_workflow/conditions/core.py backend/apps/chatbi_workflow/capabilities/adapters/recommendation.py backend/apps/chatbi_workflow/capabilities/adapters/sql.py backend/tests/chatbi_workflow
git commit -m "refactor: route chatbi consumers through execution domain"
```

### Task 6: 答案投影视图

**Files:**
- Modify: `backend/apps/chatbi_workflow/capabilities/adapters/answer.py`
- Test: `backend/tests/chatbi_workflow/test_question_rewrite_and_answer_adapters.py`

- [ ] **Step 1: 写敏感字段排除失败测试**

```python
def test_answer_projection_excludes_sql_candidate_payload_and_full_variables():
    projection = build_answer_projection(_answer_request_with_large_context())
    serialized = json.dumps(projection, ensure_ascii=False)

    assert "select secret from table" not in serialized
    assert "candidate_groups" not in serialized
    assert "slot_bindings" not in serialized
    assert "private-payload" not in serialized
    assert projection["execution"]["results"][0]["sample_rows"] == [{"value": 1}]
```

再覆盖单查询与拆分查询投影形状相同、失败摘要保留。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_question_rewrite_and_answer_adapters.py -q
```

Expected: prompt 仍包含全量 variables 和 SQL 原文。

- [ ] **Step 3: 实现纯投影函数**

```python
def build_answer_projection(ctx: ChatBIRunContext) -> dict[str, Any]:
    execution = ctx.execution
    return {
        "question": {
            "raw": ctx.raw_question,
            "rewritten": ctx.question,
        },
        "plan": _answer_plan_projection(ctx.plan),
        "execution": {
            "status": execution.get("status"),
            "results": [_answer_result_projection(item) for item in execution.get("results", [])],
            "row_count": execution.get("row_count", 0),
            "error_code": execution.get("error_code"),
            "message": execution.get("message"),
        },
        "knowledge_decision": {
            "status": ctx.knowledge.get("decision", {}).get("status"),
            "reason": ctx.knowledge.get("decision", {}).get("reason"),
        },
        "node_failure": ctx.node_failure,
    }
```

`build_answer_generation_prompt()` 只序列化 `projection`。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/chatbi_workflow/test_question_rewrite_and_answer_adapters.py tests/chatbi_workflow/test_v1_degradation.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/chatbi_workflow/capabilities/adapters/answer.py backend/tests/chatbi_workflow/test_question_rewrite_and_answer_adapters.py
git commit -m "refactor: project chatbi answer model context"
```

### Task 7: Trace 与前端统一消费

**Files:**
- Modify: `backend/apps/workflow_engine/api/service.py`
- Modify: `backend/tests/workflow_engine/test_graph_api.py`
- Modify: `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts`
- Create: `frontend/src/views/chat/execution-component/graphWorkflowDisplay.test.mjs`

- [ ] **Step 1: 写 Trace 和前端新结构失败测试**

后端测试：

```python
def test_graph_trace_projects_unified_split_execution_results():
    trace = _trace_for_split_execution()
    output = trace["nodes_by_name"]["execute_split_queries"]["output"]
    assert output == {
        "status": "succeeded",
        "query_count": 2,
        "row_count": 3,
        "fields": ["value"],
        "execution_ms": 8,
        "artifact_refs": [{"artifact_id": "artifact-1"}, {"artifact_id": "artifact-2"}],
    }
```

前端测试必须输入 `results[]` 并断言步骤摘要为“2 条查询，共返回 3 行”，详情读取每个 result 的 `sample_rows`。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py -q --tb=short
cd ../frontend
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
```

Expected: 后端拆分 trace 缺失或仍按旧 `rows` 读取；Node 测试因前端不认识 `results[]` 而失败。

- [ ] **Step 3: 实现统一 Trace 和前端兼容读取**

后端 `_sanitize_trace_output()` 对 `execute_sql` 和 `execute_split_queries` 使用同一投影函数，返回 artifact 引用数组，不返回样本行。

前端增加：

```typescript
function executionResults(output: Record<string, any>) {
  if (Array.isArray(output.results)) return output.results
  return []
}
```

新结构优先，旧 `rows` 分支保留为回退。

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py -q --tb=short
cd ../frontend
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
```

Expected: PASS。若 Graph API 仍因真实答案模型或旧断言失败，先修测试隔离与 Step 2 节点序列断言，不通过删除断言规避。

- [ ] **Step 5: 提交**

```bash
git add backend/apps/workflow_engine/api/service.py backend/tests/workflow_engine/test_graph_api.py frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts frontend/src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
git commit -m "feat: display unified chatbi execution results"
```

### Task 8: 全量回归与文档状态

**Files:**
- Modify: `docs/chatbi-v1-graph-refactor-analysis-and-design.md`
- Modify: `backend/apps/chatbi_workflow/CHATBI_V1_FLOW_TEST_RECORD.md`

- [ ] **Step 1: 更新 Step 3 状态与测试记录**

在迁移表中将 Step 3 标记为已完成，列出：

- `execution.queries[]/results[]`
- 拆分查询有界并行
- 文件 artifact store
- answer projection
- 兼容镜像保留项

不得提前标记 Step 4 至 Step 6。

- [ ] **Step 2: 运行后端格式与目标回归**

Run:

```bash
cd backend
uv run ruff check apps/chatbi_workflow apps/workflow_engine tests/chatbi_workflow tests/workflow_engine
uv run pytest tests/chatbi_workflow tests/workflow_engine tests/headless/test_semantic_sql_compiler.py tests/headless/test_sql_compiler_time_filters.py -q
```

Expected: 0 lint errors；0 test failures。若 outbox 共享数据库测试仍受历史 pending 事件影响，必须先修复测试隔离并重新运行完整命令。

- [ ] **Step 3: 运行前端检查**

Run:

```bash
cd frontend
npm run lint
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
npm run build
```

Expected: 所有命令退出码为 0。

- [ ] **Step 4: 验证 artifact 文件治理**

Run:

```bash
git status --short
find backend/data/workflow_artifacts -type f -maxdepth 1 2>/dev/null
```

Expected: 没有测试 artifact 残留；Git 状态只包含本阶段预期文件。

- [ ] **Step 5: 提交文档与最终修正**

```bash
git add -f docs/chatbi-v1-graph-refactor-analysis-and-design.md
git add backend/apps/chatbi_workflow/CHATBI_V1_FLOW_TEST_RECORD.md
git commit -m "docs: mark chatbi v1 step 3 complete"
```

- [ ] **Step 6: 最终差异审查**

Run:

```bash
git diff HEAD~8..HEAD --check
git status --short
git log --oneline -10
```

Expected: 无空白错误、无意外文件、提交顺序与任务一致。
