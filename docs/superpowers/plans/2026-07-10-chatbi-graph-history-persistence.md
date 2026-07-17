# ChatBI Graph History Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新产生的交互式 Graph ChatBI Run 与会话历史稳定关联，并在刷新、切换会话、澄清恢复、取消、重试和删除场景中保持可恢复、可展示和状态一致。

**Architecture:** `WorkflowRun` 继续作为执行事实源，`ChatRecord` 作为用户可见的稳定历史投影。交互式 Graph Chat 使用独立 API，运行时通过通用 `RunStore` 装饰器在同一 Session 中同步投影 ChatRecord；独立 Semantic Graph API 保持无会话语义。

**Tech Stack:** FastAPI、SQLModel、SQLAlchemy、Alembic、PostgreSQL JSONB、Vue 3、TypeScript、SSE、pytest、Node.js assert、vue-tsc、Vite。

## Global Constraints

- 所有新增或修改的代码必须写中文注释。
- 先用失败测试证明问题，再写最小实现；禁止先改实现再补测试。
- 小而准地修改，不进行与历史链路无关的重构。
- Graph Runtime 保持通用，不直接导入 Chat 或 ChatRecord。
- 交互式 Graph Run 必须同时拥有 `chat_id` 和 `record_id`；独立 Run 两者均为空。
- ChatRecord 是终态历史展示入口，终态页面不得依赖重新加载完整 Run 才能显示答案。
- 多轮上下文只读取同用户、同会话、同数据集最近一次成功 Graph 记录的语义摘要。
- 不自动回填或推断现有孤立 WorkflowRun 的会话归属。
- 会话删除时清理关联 Workflow 数据；独立 Semantic Run 不受影响。
- 禁止宽泛 `try/except` 吞掉投影、关联或 Artifact 清理错误。
- 禁止静默 fallback 掩盖 `chat_id`、`record_id` 或最终答案缺失。
- 处理可选依赖时禁止把导入失败对象赋值为 `None`；使用明确抛出 `ImportError` 的存根或惰性加载函数。
- 不修改或提交 `.dev-logs/backend.pid`、`.dev-logs/frontend.pid`。

---

## File Structure

### New files

- `backend/alembic/versions/078_graph_chat_history.py`：增加 Graph 会话关联列、执行类型和 Artifact 清理任务表。
- `backend/apps/workflow_engine/api/chat_history.py`：集中实现 Run 到 ChatRecord 的投影，以及投影型 RunStore 装饰器。
- `backend/apps/workflow_engine/infrastructure/artifacts/cleanup.py`：安全删除 Artifact 正文并维护可重试清理任务。
- `backend/apps/chat/services/deletion.py`：统一删除 Chat、ChatRecord、ChatLog 和关联 Workflow 数据。
- `backend/tests/chat/test_graph_chat_history_migration.py`：验证迁移内容和非破坏性策略。
- `backend/tests/workflow_engine/test_graph_chat_history_projection.py`：验证状态与结果投影。
- `backend/tests/chat/test_graph_chat_deletion.py`：验证会话级联清理和 Artifact 任务。
- `frontend/tests/graphChatHistory.test.ts`：验证前端 API、真实 record_id 和按 execution_type 渲染契约。

### Modified files

- `backend/apps/chat/models/chat_model.py`：增加 `execution_type` 字段和 DTO 输出。
- `backend/apps/agentic_chat/crud.py`：创建 Agentic 记录时写入 `execution_type=agentic`。
- `backend/apps/chat/curd/chat.py`：传统记录写入 `legacy`，历史查询返回 Graph 字段，删除入口改用统一服务。
- `backend/apps/chat/api/chat.py`：删除接口使用统一删除服务，并保留明确错误语义。
- `backend/apps/workflow_engine/infrastructure/persistence/models.py`：增加 Run 关联列和清理任务模型。
- `backend/apps/workflow_engine/infrastructure/persistence/run_repository.py`：从 Context request 写入 Run 的物理关联列。
- `backend/apps/workflow_engine/infrastructure/artifacts/file_store.py`：统一 Artifact 根目录和安全文件删除逻辑。
- `backend/apps/workflow/runtime.py`：允许应用层注入 RunStore，不改变 Graph Runtime 领域协议。
- `backend/apps/workflow_engine/api/schemas.py`：拆分交互式请求契约并返回 `record_id`。
- `backend/apps/workflow_engine/api/router.py`：增加 `/graph/chats/{chat_id}/queries` 及流式入口。
- `backend/apps/workflow_engine/api/service.py`：区分独立与交互式创建，统一接入投影和生命周期同步。
- `backend/main.py`：启动时重试未完成 Artifact 清理任务。
- `backend/tests/workflow_engine/test_persistence_models.py`：验证新字段、索引和表。
- `backend/tests/workflow_engine/test_graph_api.py`：迁移现有会话 Graph 测试到交互式入口，并补充生命周期断言。
- `backend/tests/chat/test_headless_dataset_chat.py`：验证传统 ChatRecord 的执行类型。
- `frontend/src/api/chat.ts`：增加 `execution_type` 映射。
- `frontend/src/api/graph-workflow.ts`：增加交互式 Graph Chat API 和 `record_id` 响应。
- `frontend/src/views/chat/index.vue`：按记录执行类型选择回答组件。
- `frontend/src/views/chat/answer/GraphWorkflowAnswer.vue`：调用交互式 API、接收真实 record_id、按终态/非终态恢复。

---

### Task 1: Add persistence schema for Graph chat ownership

**Files:**
- Create: `backend/alembic/versions/078_graph_chat_history.py`
- Create: `backend/tests/chat/test_graph_chat_history_migration.py`
- Modify: `backend/apps/chat/models/chat_model.py:105-170`
- Modify: `backend/apps/workflow_engine/infrastructure/persistence/models.py:47-207`
- Modify: `backend/tests/workflow_engine/test_persistence_models.py`

**Interfaces:**
- Produces: `ChatRecord.execution_type: str`
- Produces: `WorkflowRunModel.chat_id: int | None`
- Produces: `WorkflowRunModel.record_id: int | None`
- Produces: `WorkflowArtifactCleanupModel`

- [ ] **Step 1: Write failing model tests**

Add imports and assertions to `backend/tests/workflow_engine/test_persistence_models.py`:

```python
from apps.workflow_engine.infrastructure.persistence.models import (
    WorkflowArtifactCleanupModel,
    WorkflowRunModel,
)


def test_workflow_run_declares_chat_ownership_columns_and_indexes():
    columns = WorkflowRunModel.__table__.c

    assert columns.chat_id.nullable is True
    assert columns.record_id.nullable is True
    assert {
        "idx_workflow_run_chat",
        "ux_workflow_run_record",
    }.issubset(_index_names(WorkflowRunModel))


def test_workflow_artifact_cleanup_model_is_retryable():
    assert WorkflowArtifactCleanupModel.__tablename__ == "workflow_artifact_cleanup"
    columns = WorkflowArtifactCleanupModel.__table__.c
    assert columns.artifact_id.nullable is False
    assert columns.status.nullable is False
    assert columns.attempts.nullable is False
```

Create `backend/tests/chat/test_graph_chat_history_migration.py`:

```python
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "078_graph_chat_history.py"
)


def test_graph_chat_history_migration_adds_non_destructive_schema():
    content = MIGRATION.read_text()

    assert 'revision = "078_graph_chat_history"' in content
    assert 'down_revision = "077_headless_metric_embedding"' in content
    assert 'op.add_column("chat_record"' in content
    assert '"execution_type"' in content
    assert 'op.add_column("workflow_run"' in content
    assert '"chat_id"' in content
    assert '"record_id"' in content
    assert 'op.create_table("workflow_artifact_cleanup"' in content
    assert "DELETE FROM chat" not in content
    assert "UPDATE workflow_run" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_persistence_models.py tests/chat/test_graph_chat_history_migration.py -q
```

Expected: FAIL because the migration, columns and cleanup model do not exist.

- [ ] **Step 3: Add SQLModel fields and migration**

Add to `ChatRecord`:

```python
execution_type: str = Field(default="legacy", max_length=32, nullable=False)
```

Add to `ChatRecordResult`:

```python
execution_type: str | None = None
```

Add indexes and columns to `WorkflowRunModel`:

```python
__table_args__ = (
    Index("idx_workflow_run_status", "oid", "status", text("updated_at DESC")),
    Index("idx_workflow_run_definition", "definition_name", "definition_version"),
    Index("idx_workflow_run_request", "request_id"),
    Index("idx_workflow_run_chat", "chat_id", text("created_at DESC")),
    Index("ux_workflow_run_record", "record_id", unique=True),
)

chat_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
record_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
```

Add the cleanup model:

```python
class WorkflowArtifactCleanupModel(SQLModel, table=True):
    """记录会话删除后需要清理的 Artifact 正文。"""

    __tablename__ = "workflow_artifact_cleanup"
    __table_args__ = (
        Index("idx_workflow_artifact_cleanup_status", "status", "updated_at"),
    )

    id: int | None = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    artifact_id: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    storage_uri: str = Field(sa_column=Column(String(512), nullable=False))
    status: str = Field(
        default="pending",
        sa_column=Column(String(32), nullable=False, server_default=text("'pending'")),
    )
    attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
```

Create migration `078_graph_chat_history.py` with these operations:

```python
def upgrade():
    op.add_column(
        "chat_record",
        sa.Column("execution_type", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE chat_record AS record
        SET execution_type = 'agentic'
        WHERE EXISTS (
            SELECT 1 FROM agentic_run AS run WHERE run.record_id = record.id
        )
        """
    )
    op.execute(
        "UPDATE chat_record SET execution_type = 'legacy' WHERE execution_type IS NULL"
    )
    op.alter_column("chat_record", "execution_type", nullable=False, server_default="legacy")

    op.add_column("workflow_run", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("workflow_run", sa.Column("record_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_workflow_run_chat",
        "workflow_run",
        ["chat_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ux_workflow_run_record",
        "workflow_run",
        ["record_id"],
        unique=True,
    )

    op.create_table(
        "workflow_artifact_cleanup",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
    )
    op.create_index(
        "idx_workflow_artifact_cleanup_status",
        "workflow_artifact_cleanup",
        ["status", "updated_at"],
    )
```

Use this downgrade order and do not backfill `workflow_run.chat_id` or `record_id` from JSON:

```python
def downgrade():
    op.drop_index("idx_workflow_artifact_cleanup_status", table_name="workflow_artifact_cleanup")
    op.drop_table("workflow_artifact_cleanup")
    op.drop_index("ux_workflow_run_record", table_name="workflow_run")
    op.drop_index("idx_workflow_run_chat", table_name="workflow_run")
    op.drop_column("workflow_run", "record_id")
    op.drop_column("workflow_run", "chat_id")
    op.drop_column("chat_record", "execution_type")
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_persistence_models.py tests/chat/test_graph_chat_history_migration.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit schema changes**

```bash
git add -f backend/alembic/versions/078_graph_chat_history.py backend/tests/chat/test_graph_chat_history_migration.py backend/tests/workflow_engine/test_persistence_models.py
git add backend/apps/chat/models/chat_model.py backend/apps/workflow_engine/infrastructure/persistence/models.py
git commit -m "feat: add graph chat history persistence schema"
```

---

### Task 2: Implement the single Run-to-ChatRecord projection entry

**Files:**
- Create: `backend/apps/workflow_engine/api/chat_history.py`
- Create: `backend/tests/workflow_engine/test_graph_chat_history_projection.py`
- Modify: `backend/apps/workflow_engine/infrastructure/persistence/run_repository.py`

**Interfaces:**
- Produces: `GraphResultNotProjectableError`
- Produces: `GraphChatRecordProjector.project(run: WorkflowRun) -> ChatRecord | None`
- Produces: `GraphChatRecordProjector.project_model(run: WorkflowRunModel) -> ChatRecord | None`
- Produces: `ChatProjectingRunStore(RunStore)`
- Consumes: Task 1 model columns and `ChatRecord.execution_type`

- [ ] **Step 1: Write failing projector tests**

Create these helpers at the top of the test file:

```python
def seed_graph_record(session: Session) -> tuple[Chat, ChatRecord]:
    now = datetime.now()
    chat = Chat(
        oid=1,
        create_time=now,
        create_by=10,
        brief="projection-chat",
        chat_type="chat",
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
    )
    session.add(chat)
    session.flush()
    record = ChatRecord(
        chat_id=chat.id or 0,
        create_time=now,
        create_by=10,
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
        execution_type="graph",
        question="本月销售额",
        finish=False,
        status="created",
        trace_id="projection-run",
    )
    session.add(record)
    session.flush()
    return chat, record


def workflow_run(
    *,
    record_id: int | None,
    chat_id: int | None,
    status: RunStatus,
    variables: dict,
) -> WorkflowRun:
    now = datetime.now(timezone.utc)
    request = {"tenant_id": 1, "user_id": 10, "dataset_id": 20}
    if chat_id is not None:
        request["chat_id"] = chat_id
    if record_id is not None:
        request["record_id"] = record_id
    return WorkflowRun(
        run_id="projection-run",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="projection-test",
        status=status,
        current_node="finish",
        context=WorkflowContext(request=request, variables=variables),
        version=1,
        created_at=now,
        updated_at=now,
    )
```

Then add tests covering successful, waiting, failed and standalone runs:

```python
def test_projector_writes_success_snapshot(session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.SUCCEEDED,
        variables={
            "final_reply": {"final_answer": "本月销售额为 100 元。", "chart": {"type": "table"}},
            "sql": {"sql": "select 100 as sales"},
        },
    )

    projected = GraphChatRecordProjector(session).project(run)

    assert projected.status == "succeeded"
    assert projected.finish is True
    assert projected.sql_answer == "本月销售额为 100 元。"
    assert projected.sql == "select 100 as sales"
    assert projected.execution_type == "graph"


def test_projector_rejects_success_without_displayable_answer(session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.SUCCEEDED,
        variables={"final_reply": {}},
    )

    with pytest.raises(GraphResultNotProjectableError, match="GRAPH_RESULT_NOT_PROJECTABLE"):
        GraphChatRecordProjector(session).project(run)


def test_projector_keeps_waiting_record_recoverable(session):
    chat, record = seed_graph_record(session)
    run = workflow_run(
        record_id=record.id,
        chat_id=chat.id,
        status=RunStatus.WAITING_INPUT,
        variables={},
    )

    projected = GraphChatRecordProjector(session).project(run)

    assert projected.status == "waiting_input"
    assert projected.finish is False
    assert projected.trace_id == run.run_id


def test_projector_ignores_standalone_run(session):
    run = workflow_run(record_id=None, chat_id=None, status=RunStatus.SUCCEEDED, variables={})

    assert GraphChatRecordProjector(session).project(run) is None
```

Also test that `ChatProjectingRunStore.save()` projects before the caller commits.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_chat_history_projection.py -q
```

Expected: FAIL because the projector and RunStore decorator do not exist.

- [ ] **Step 3: Implement projector and projecting RunStore**

Create `chat_history.py` with this public shape:

```python
class GraphResultNotProjectableError(RuntimeError):
    """Graph 成功但无法形成用户可见历史快照。"""


class GraphChatRecordProjector:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project(self, run: WorkflowRun) -> ChatRecord | None:
        record_id = run.context.request.get("record_id")
        chat_id = run.context.request.get("chat_id")
        if record_id is None and chat_id is None:
            return None
        if record_id is None or chat_id is None:
            raise GraphResultNotProjectableError("GRAPH_CHAT_OWNERSHIP_INCOMPLETE")

        record = self._session.get(ChatRecord, int(record_id))
        if record is None or record.chat_id != int(chat_id):
            raise GraphResultNotProjectableError("GRAPH_CHAT_RECORD_NOT_FOUND")

        record.trace_id = run.run_id
        record.execution_type = "graph"
        record.status = run.status.value
        record.finish = run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        record.finish_time = datetime.now() if record.finish else None

        if run.status is RunStatus.SUCCEEDED:
            variables = run.context.variables
            final_reply = variables.get("final_reply") or {}
            legacy_answer = variables.get("answer") or {}
            final_answer = str(
                final_reply.get("final_answer") or legacy_answer.get("answer") or ""
            ).strip()
            if not final_answer:
                raise GraphResultNotProjectableError("GRAPH_RESULT_NOT_PROJECTABLE")
            record.sql_answer = final_answer
            sql_payload = variables.get("sql") or {}
            record.sql = sql_payload.get("sql") if isinstance(sql_payload, dict) else None
            chart = final_reply.get("chart")
            record.chart = orjson.dumps(chart).decode() if chart is not None else None
            record.error = None
        elif run.status is RunStatus.FAILED:
            record.error = "GRAPH_RUN_FAILED"
        elif run.status in {RunStatus.CREATED, RunStatus.RUNNING}:
            # 重试沿用原记录，进入运行态时清理旧终态快照。
            record.finish = False
            record.finish_time = None
            record.error = None

        self._session.add(record)
        self._session.flush()
        return record

    def project_model(self, run: WorkflowRunModel) -> ChatRecord | None:
        return self.project(RunRepository(self._session).to_domain(run))
```

Expose a public `to_domain()` method from `RunRepository`; do not duplicate ORM-to-domain conversion.

Implement the decorator:

```python
class ChatProjectingRunStore:
    """在 Run 保存事务中同步维护 ChatRecord 历史投影。"""

    def __init__(self, base: RunStore, projector: GraphChatRecordProjector) -> None:
        self._base = base
        self._projector = projector

    def create(self, run: WorkflowRun) -> WorkflowRun:
        created = self._base.create(run)
        self._projector.project(created)
        return created

    def get(self, run_id: str) -> WorkflowRun:
        return self._base.get(run_id)

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun:
        saved = self._base.save(run, expected_version)
        self._projector.project(saved)
        return saved
```

The decorator must not call `commit()`; the existing event publisher or outer application service owns commit timing.

- [ ] **Step 4: Run projector tests**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_chat_history_projection.py tests/workflow_engine/test_run_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit projection boundary**

```bash
git add -f backend/tests/workflow_engine/test_graph_chat_history_projection.py
git add backend/apps/workflow_engine/api/chat_history.py backend/apps/workflow_engine/infrastructure/persistence/run_repository.py
git commit -m "feat: project graph runs into chat history"
```

---

### Task 3: Separate interactive Graph Chat API from standalone Graph execution

**Files:**
- Modify: `backend/apps/workflow_engine/api/schemas.py`
- Modify: `backend/apps/workflow_engine/api/router.py`
- Modify: `backend/apps/workflow_engine/api/service.py`
- Modify: `backend/apps/workflow/runtime.py`
- Modify: `backend/apps/workflow_engine/infrastructure/persistence/run_repository.py`
- Modify: `backend/tests/workflow_engine/test_graph_api.py`

**Interfaces:**
- Produces: `GraphChatQueryRequest`
- Produces: `GraphRunResponse.record_id: int | None`
- Produces: `GraphApiService.create_chat_query(current_user, chat_id, request, commit_events=False)`
- Produces: `GraphApiService.stream_chat_query(current_user, chat_id, request)`
- Consumes: `ChatProjectingRunStore` from Task 2

- [ ] **Step 1: Write failing route and ownership tests**

Add these helpers to `backend/tests/workflow_engine/test_graph_api.py`; reuse the existing `_seed_v1_headless_dataset()` function:

```python
def _seed_graph_chat() -> tuple[int, int]:
    now = datetime.now()
    with Session(engine) as session:
        _cleanup(session)
        dataset_id = _seed_v1_headless_dataset(session)
        chat = Chat(
            oid=9501,
            create_time=now,
            create_by=501,
            brief="api_graph_owned_chat",
            chat_type="chat",
            dataset_id=dataset_id,
            datasource=7001,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)
        return chat.id or 0, dataset_id


def _load_pending_interaction_id(run_id: str) -> str:
    with Session(engine) as session:
        interaction = session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == run_id,
                InteractionRequestModel.status == "pending",
            )
        ).one()
        return interaction.interaction_id


def _load_chat_record(record_id: int) -> ChatRecord:
    with Session(engine) as session:
        record = session.get(ChatRecord, record_id)
        assert record is not None
        session.expunge(record)
        return record
```

Extend the existing `_cleanup()` chat brief filter to include `"api_graph_owned_chat"`, so every Graph API test removes its owned ChatRecord rows as well as its `api-%` Workflow rows.

Extend the expected route set:

```python
expected_routes = {
    "/graph/queries",
    "/graph/queries/stream",
    "/graph/chats/{chat_id}/queries",
    "/graph/chats/{chat_id}/queries/stream",
}
```

Add an interactive API test:

```python
def test_graph_chat_query_creates_owned_record_and_run():
    chat_id, dataset_id = _seed_graph_chat()

    response = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "本月销售额",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-owned-run",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record_id"] is not None

    with Session(engine) as session:
        run = session.exec(
            select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-owned-run")
        ).one()
        record = session.get(ChatRecord, body["record_id"])
        assert run.chat_id == chat_id
        assert run.record_id == record.id
        assert record.trace_id == run.run_id
        assert record.execution_type == "graph"
```

Add a standalone assertion to the existing `/graph/queries` test:

```python
assert stored.chat_id is None
assert stored.record_id is None
```

- [ ] **Step 2: Run focused API tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py::test_graph_routes_are_registered_and_included_by_apps_api tests/workflow_engine/test_graph_api.py::test_graph_chat_query_creates_owned_record_and_run -q
```

Expected: FAIL because the interactive routes and `record_id` response do not exist.

- [ ] **Step 3: Add request/response schemas and routes**

In `schemas.py`:

```python
class GraphQueryRequest(BaseModel):
    """独立 Graph Run 请求，不创建聊天历史。"""

    question: str = Field(min_length=1)
    dataset_id: int = Field(gt=0)
    definition_version: Literal["minimal-v1", "v1"] = "minimal-v1"
    request_id: str | None = None
    run_id: str | None = None


class GraphChatQueryRequest(GraphQueryRequest):
    """交互式聊天 Graph 请求，chat_id 由路径提供。"""

    definition_version: Literal["v1"] = "v1"


class GraphRunResponse(BaseModel):
    run_id: str
    record_id: int | None = None
    status: str
    current_node: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    context_summary: dict[str, Any] = Field(default_factory=dict)
```

In `router.py` add both regular and streaming interactive routes. The path parameter is the only source of `chat_id`:

```python
@router.post("/chats/{chat_id}/queries", response_model=GraphRunResponse)
def create_chat_query(
    session: SessionDep,
    current_user: CurrentUser,
    chat_id: int,
    request: GraphChatQueryRequest,
):
    return GraphApiService(session).create_chat_query(current_user, chat_id, request)


@router.post("/chats/{chat_id}/queries/stream")
def stream_chat_query(
    session: SessionDep,
    current_user: CurrentUser,
    chat_id: int,
    request: GraphChatQueryRequest,
):
    return StreamingResponse(
        GraphApiService(session).stream_chat_query(current_user, chat_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Make runtime assembly accept an injected RunStore**

Update runtime builders without changing GraphRuntime:

```python
def build_real_chatbi_v1_runtime(
    session: Session,
    question_model_client: QuestionClassificationModelClient | None = None,
    answer_model_client: AnswerModelClient | None = None,
    commit_events: bool = False,
    run_store: RunStore | None = None,
) -> GraphRuntime:
    # 应用层可以注入带历史投影的 RunStore，独立执行仍使用默认仓储。
    return _build_chatbi_v1_runtime(
        session,
        gateway,
        commit_events=commit_events,
        run_store=run_store,
    )


def _build_chatbi_v1_runtime(
    session: Session,
    gateway,
    commit_events: bool = False,
    run_store: RunStore | None = None,
) -> GraphRuntime:
    effective_run_store = run_store or RunRepository(session)
    events = DatabaseEventPublisher(session, commit_on_publish=commit_events)
    return GraphRuntime(
        registry=registry,
        run_store=effective_run_store,
        checkpoint_manager=CheckpointManager(effective_run_store, events),
    )
```

Only replace the existing `RunRepository(session)` local with `effective_run_store` in the full constructor. Preserve the existing scheduler, router, context patcher, lease, interaction manager and node execution recorder arguments exactly.

- [ ] **Step 5: Implement interactive creation and physical ownership**

Make `RunRepository.create()` copy ownership from the request context:

```python
chat_id = run.context.request.get("chat_id")
record_id = run.context.request.get("record_id")
model = WorkflowRunModel(
    chat_id=int(chat_id) if chat_id is not None else None,
    record_id=int(record_id) if record_id is not None else None,
)
```

Add these two keyword arguments to the existing `WorkflowRunModel(...)` constructor; do not create a second model instance.

In `GraphApiService`, keep `create_query()` standalone and add an explicit chat method:

```python
def create_query(
    self,
    current_user: Any,
    request: GraphQueryRequest,
    commit_events: bool = False,
) -> GraphRunResponse:
    run_id = request.run_id or f"graph-{uuid4().hex}"
    dataset_id = self._resolve_dataset_id(current_user.oid, request.dataset_id)
    request_context = {
        "tenant_id": current_user.oid,
        "user_id": current_user.id,
        "question": request.question,
        "dataset_id": dataset_id,
        "source_dataset_id": request.dataset_id,
        "request_id": request.request_id,
    }
    if request.definition_version == "minimal-v1":
        request_context["datasource_id"] = request.dataset_id
    runtime = self._build_runtime(request.definition_version, commit_events=commit_events)
    created = runtime.create_run(
        run_id=run_id,
        definition_name="chatbi",
        definition_version=request.definition_version,
        context=WorkflowContext(
            request=request_context,
            conversation={"question": request.question},
        ),
    )
    runtime.execute(created.run_id)
    self._session.commit()
    return self._to_run_response(self._load_owned_run(current_user, created.run_id))
```

The standalone path must not call `_create_chat_record_for_query()` or previous-chat semantic lookup.

Add the interactive method:

```python
def create_chat_query(
    self,
    current_user: Any,
    chat_id: int,
    request: GraphChatQueryRequest,
    commit_events: bool = False,
) -> GraphRunResponse:
    run_id = request.run_id or f"graph-{uuid4().hex}"
    dataset_id = self._resolve_dataset_id(current_user.oid, request.dataset_id)
    chat = self._session.get(Chat, chat_id)
    if chat is None or chat.oid != current_user.oid or chat.create_by != current_user.id:
        raise HTTPException(status_code=404, detail="CHAT_NOT_FOUND")
    if chat.dataset_id is None or int(chat.dataset_id) != dataset_id:
        raise HTTPException(status_code=400, detail="CHAT_DATASET_MISMATCH")

    record = ChatRecord(
        chat_id=chat_id,
        create_time=datetime.now(),
        create_by=current_user.id,
        dataset_id=dataset_id,
        datasource=chat.datasource,
        engine_type=chat.engine_type,
        execution_type="graph",
        question=request.question,
        finish=False,
        status=RunStatus.CREATED.value,
        trace_id=run_id,
    )
    self._session.add(record)
    self._session.flush()
    request_context = {
        "tenant_id": current_user.oid,
        "user_id": current_user.id,
        "question": request.question,
        "dataset_id": dataset_id,
        "source_dataset_id": request.dataset_id,
        "request_id": request.request_id,
        "chat_id": chat_id,
        "record_id": record.id,
    }
    run_store = ChatProjectingRunStore(
        RunRepository(self._session),
        GraphChatRecordProjector(self._session),
    )
    runtime = build_real_chatbi_v1_runtime(
        self._session,
        commit_events=commit_events,
        run_store=run_store,
    )
    created = runtime.create_run(
        run_id=run_id,
        definition_name="chatbi",
        definition_version=request.definition_version,
        context=WorkflowContext(
            request=request_context,
            conversation=self._build_conversation_context(
                current_user=current_user,
                question=request.question,
                dataset_id=dataset_id,
                chat_record=record,
            ),
        ),
    )
    runtime.execute(created.run_id)
    self._session.commit()
    return self._to_run_response(self._load_owned_run(current_user, created.run_id))
```

Add the ownership field in `_to_run_response()`:

```python
return GraphRunResponse(
    run_id=run.run_id,
    record_id=run.record_id,
    status=run.status,
    current_node=run.current_node,
    output=run.output or {},
    context_summary={
        "question": request.get("question"),
        "dataset_id": request.get("dataset_id"),
        "variables": variables,
        "pending_interaction": pending_interaction.model_dump(mode="json")
        if pending_interaction is not None
        else None,
    },
)
```

Keep the existing `request`, `variables` and `pending_interaction` local calculations immediately before this return. Do not accept a body-level `chat_id` on the standalone request.

- [ ] **Step 6: Implement streaming interactive creation**

Add `stream_chat_query()` by reusing the existing worker/SSE loop, but invoke `create_chat_query()` in the worker:

```python
async def stream_chat_query(
    self,
    current_user: Any,
    chat_id: int,
    request: GraphChatQueryRequest,
) -> AsyncIterator[str]:
    run_id = request.run_id or f"graph-{uuid4().hex}"
    stream_request = request.model_copy(update={"run_id": run_id})
    errors: list[Exception] = []

    def execute_query() -> None:
        with Session(engine) as session:
            try:
                GraphApiService(session).create_chat_query(
                    current_user,
                    chat_id,
                    stream_request,
                    commit_events=True,
                )
            except Exception as exc:
                session.rollback()
                errors.append(exc)

    worker = threading.Thread(target=execute_query, daemon=True)
    worker.start()
    async for frame in self._stream_run_events(
        current_user,
        run_id,
        after_sequence=0,
        worker=worker,
        errors=errors,
    ):
        yield frame
```

Keep `_stream_run_events` unchanged. The worker catch remains only to transport the original exception through the existing SSE error frame; explicit application errors must still be raised before this boundary.

- [ ] **Step 7: Run Graph API tests**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit interactive API**

```bash
git add backend/apps/workflow_engine/api/schemas.py backend/apps/workflow_engine/api/router.py backend/apps/workflow_engine/api/service.py backend/apps/workflow/runtime.py backend/apps/workflow_engine/infrastructure/persistence/run_repository.py
git add -f backend/tests/workflow_engine/test_graph_api.py
git commit -m "feat: add owned graph chat query api"
```

---

### Task 4: Apply projection to resume, cancel, retry and previous-context lookup

**Files:**
- Modify: `backend/apps/workflow_engine/api/service.py`
- Modify: `backend/tests/workflow_engine/test_graph_api.py`
- Modify: `backend/tests/workflow_engine/test_graph_chat_history_projection.py`

**Interfaces:**
- Consumes: `GraphChatRecordProjector`
- Consumes: `ChatProjectingRunStore`
- Produces: consistent lifecycle projection for all control operations

- [ ] **Step 1: Write failing lifecycle tests**

Add assertions to the interaction test using the interactive route:

```python
def test_graph_chat_interaction_resume_updates_same_record():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-clarify",
        },
    )
    record_id = created.json()["record_id"]
    interaction_id = _load_pending_interaction_id("api-chat-clarify")

    answered = _client().post(
        f"/graph/runs/api-chat-clarify/interactions/{interaction_id}/responses",
        json={"response": {"metric": "sales_amount"}},
    )

    assert answered.status_code == 200
    with Session(engine) as session:
        record = session.get(ChatRecord, record_id)
        assert record.status == "succeeded"
        assert record.finish is True
        assert record.sql_answer
```

Add cancel and retry tests that assert the same `record_id` is reused:

```python
def test_graph_chat_cancel_projects_cancelled_status():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-cancel",
        },
    )
    record_id = created.json()["record_id"]

    cancelled = _client().post("/graph/runs/api-chat-cancel/cancel")

    assert cancelled.status_code == 200
    record = _load_chat_record(record_id)
    assert record.status == "cancelled"
    assert record.finish is True


def test_graph_chat_retry_reuses_record_and_clears_error():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-chat-retry",
        },
    )
    record_id = created.json()["record_id"]
    with Session(engine) as session:
        run = session.exec(
            select(WorkflowRunModel).where(WorkflowRunModel.run_id == "api-chat-retry")
        ).one()
        record = session.get(ChatRecord, record_id)
        run.status = "failed"
        record.status = "failed"
        record.finish = True
        record.error = "OLD_ERROR"
        session.add(run)
        session.add(record)
        session.commit()

    retried = _client().post("/graph/runs/api-chat-retry/retry")

    assert retried.status_code == 200
    record = _load_chat_record(record_id)
    assert record.id == record_id
    assert record.status == "succeeded"
    assert record.error is None
```

Extend the test module import to `from datetime import datetime, timedelta`, then insert a newer failed Graph ChatRecord and failed WorkflowRun for the same chat:

```python
failed_record = ChatRecord(
    chat_id=chat.id or 0,
    create_time=now + timedelta(seconds=1),
    finish_time=now + timedelta(seconds=1),
    create_by=501,
    dataset_id=dataset_id,
    datasource=7001,
    engine_type="PostgreSQL",
    execution_type="graph",
    question="失败的后续问题",
    finish=True,
    status="failed",
    trace_id="api-failed-context",
)
session.add(failed_record)
session.flush()
session.add(
    WorkflowRunModel(
        run_id="api-failed-context",
        oid=9501,
        user_id=501,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="test",
        status="failed",
        chat_id=chat.id,
        record_id=failed_record.id,
        context={"request": {"dataset_id": dataset_id}},
        request={"dataset_id": dataset_id},
        output={},
        version=1,
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
)
```

Retain these assertions after creating the next query:

```python
assert context["conversation"]["last_run_id"] == "api-prev-context"
assert context["conversation"]["last_question"] == "今天店铺的访问人数"
assert context["conversation"]["last_intent"]["metric_mentions"] == ["访问人数"]
```

This proves the lookup skips the newer failed record rather than treating recency alone as success.

- [ ] **Step 2: Run lifecycle tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py -k "chat and (interaction or cancel or retry or semantic_context)" -q
```

Expected: FAIL because control paths do not consistently project ChatRecord.

- [ ] **Step 3: Build projecting runtime for linked runs**

Add a single helper in `GraphApiService`:

```python
def _build_runtime_for_run(
    self,
    run: WorkflowRunModel,
    *,
    commit_events: bool = False,
):
    if run.record_id is not None:
        run_store = ChatProjectingRunStore(
            RunRepository(self._session),
            GraphChatRecordProjector(self._session),
        )
        if run.definition_version != "v1":
            raise HTTPException(status_code=400, detail="GRAPH_CHAT_DEFINITION_UNSUPPORTED")
        return build_real_chatbi_v1_runtime(
            self._session,
            commit_events=commit_events,
            run_store=run_store,
        )
    return self._build_runtime(run.definition_version, commit_events=commit_events)
```

Use this helper in `answer_interaction()` and `retry()`. After direct model mutations in `cancel()` and the retry reset phase, call `GraphChatRecordProjector.project_model(run)` before commit.

- [ ] **Step 4: Tighten previous semantic context query**

Query through physical ownership and Graph execution type:

```python
records = self._session.exec(
    select(ChatRecord)
    .where(
        ChatRecord.chat_id == chat_record.chat_id,
        ChatRecord.id != chat_record.id,
        ChatRecord.execution_type == "graph",
        ChatRecord.status == RunStatus.SUCCEEDED.value,
        col(ChatRecord.finish).is_(True),
        col(ChatRecord.trace_id).is_not(None),
    )
    .order_by(col(ChatRecord.create_time).desc(), col(ChatRecord.id).desc())
    .limit(10)
).all()
```

When loading the Run, require `WorkflowRunModel.record_id == record.id`, matching user, tenant, chat and dataset. Continue exposing only the approved intent summary keys.

- [ ] **Step 5: Map projection errors explicitly**

At the application boundary, translate only `GraphResultNotProjectableError`:

```python
except GraphResultNotProjectableError as exc:
    self._session.rollback()
    raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Do not catch unrelated exceptions here. Existing worker code may transport the explicit error through SSE.

- [ ] **Step 6: Run lifecycle and projection tests**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_chat_history_projection.py tests/workflow_engine/test_graph_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit lifecycle consistency**

```bash
git add backend/apps/workflow_engine/api/service.py
git add -f backend/tests/workflow_engine/test_graph_api.py backend/tests/workflow_engine/test_graph_chat_history_projection.py
git commit -m "fix: keep graph chat lifecycle history consistent"
```

---

### Task 5: Return stable Graph snapshots from chat history

**Files:**
- Modify: `backend/apps/chat/curd/chat.py:351-475`
- Modify: `backend/apps/agentic_chat/crud.py:31-80`
- Modify: `backend/tests/chat/test_headless_dataset_chat.py`
- Create: `backend/tests/chat/test_graph_chat_history.py`

**Interfaces:**
- Produces: chat history records with `execution_type`, `status` and `trace_id`
- Consumes: Task 1 ChatRecord schema

- [ ] **Step 1: Write failing history serialization tests**

Create a direct CRUD test with a seeded Graph record:

```python
def test_get_chat_with_records_returns_graph_snapshot():
    current_user = SimpleNamespace(id=9101, oid=9201)
    now = datetime.now()
    with Session(engine) as session:
        chat = Chat(
            oid=current_user.oid,
            create_by=current_user.id,
            create_time=now,
            brief="graph-history-test",
            chat_type="chat",
            dataset_id=None,
            datasource=None,
            engine_type="PostgreSQL",
        )
        session.add(chat)
        session.flush()
        record = ChatRecord(
            chat_id=chat.id or 0,
            create_by=current_user.id,
            create_time=now,
            dataset_id=None,
            datasource=None,
            engine_type=chat.engine_type,
            execution_type="graph",
            question="本月销售额",
            sql_answer="本月销售额为 100 元。",
            sql="select 100 as sales",
            status="succeeded",
            trace_id="graph-history-1",
            finish=True,
        )
        session.add(record)
        session.commit()

        result = get_chat_with_records(
            session=session,
            chart_id=chat.id or 0,
            current_user=current_user,
            current_assistant=None,
        )

        graph_record = next(
            item for item in result.records if item["trace_id"] == "graph-history-1"
        )
        assert graph_record["execution_type"] == "graph"
        assert graph_record["status"] == "succeeded"
        assert graph_record["sql_answer"] == "本月销售额为 100 元。"

        session.delete(record)
        session.delete(chat)
        session.commit()
```

Extend `backend/tests/chat/test_headless_dataset_chat.py` with explicit execution-type assertions:

```python
def test_save_question_marks_legacy_execution_type(monkeypatch):
    chat_crud = import_chat_crud(monkeypatch)
    chat = Chat(id=77, create_by=10, oid=1, dataset_id=20, datasource=40, engine_type="MySQL")
    session = FakeSession(chat)

    record = chat_crud.save_question(
        session,
        make_user(),
        ChatQuestion(chat_id=77, question="销售额是多少"),
    )

    assert record.execution_type == "legacy"


def test_agentic_create_marks_agentic_execution_type():
    from apps.agentic_chat.crud import create_record_and_run
    from apps.agentic_chat.schemas import AgenticQuestionRequest

    chat = Chat(id=78, create_by=10, oid=1, datasource=40, engine_type="MySQL")
    session = FakeSession(chat)

    record, _run = create_record_and_run(
        session,
        make_user(),
        AgenticQuestionRequest(chat_id=78, question="销售额是多少"),
        config={},
    )

    assert record.execution_type == "agentic"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/chat/test_graph_chat_history.py tests/chat/test_headless_dataset_chat.py -q
```

Expected: FAIL because history queries omit the new fields and legacy creation does not set execution type.

- [ ] **Step 3: Add execution type on record creation**

In Agentic creation:

```python
execution_type="agentic",
```

Add this keyword argument to the existing `ChatRecord(...)` constructor without changing its other arguments.

In traditional `create_chat()`, `save_question()` and analysis/predict record creation, set `execution_type="legacy"`.

- [ ] **Step 4: Return Graph fields from both history query variants**

Add these columns to both `with_data=False` and `with_data=True` selects:

```python
ChatRecord.execution_type,
ChatRecord.status,
ChatRecord.trace_id,
```

Pass the values into every `ChatRecordResult(...)` construction:

```python
execution_type=row.execution_type,
status=row.status,
trace_id=row.trace_id,
```

Do not add a per-record Run query; history must remain a single ChatRecord projection query plus existing ChatLog aggregation.

- [ ] **Step 5: Run chat history tests**

Run:

```bash
cd backend
uv run pytest tests/chat/test_graph_chat_history.py tests/chat/test_headless_dataset_chat.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit stable history response**

```bash
git add backend/apps/chat/curd/chat.py backend/apps/agentic_chat/crud.py
git add -f backend/tests/chat/test_graph_chat_history.py backend/tests/chat/test_headless_dataset_chat.py
git commit -m "feat: expose graph snapshots in chat history"
```

---

### Task 6: Use owned Graph Chat API and render history by record type

**Files:**
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/api/graph-workflow.ts`
- Modify: `frontend/src/views/chat/index.vue`
- Modify: `frontend/src/views/chat/answer/GraphWorkflowAnswer.vue`
- Create: `frontend/tests/graphChatHistory.test.ts`

**Interfaces:**
- Consumes: `GraphRunResponse.record_id`
- Produces: `graphWorkflowApi.streamChatQuery(chatId, data, handlers)`
- Produces: `ChatRecord.execution_type`

- [ ] **Step 1: Write failing frontend contract test**

Create `frontend/tests/graphChatHistory.test.ts`:

```typescript
import * as assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const apiSource = readFileSync(resolve(__dirname, '../src/api/graph-workflow.ts'), 'utf8')
const answerSource = readFileSync(
  resolve(__dirname, '../src/views/chat/answer/GraphWorkflowAnswer.vue'),
  'utf8'
)
const indexSource = readFileSync(resolve(__dirname, '../src/views/chat/index.vue'), 'utf8')
const chatApiSource = readFileSync(resolve(__dirname, '../src/api/chat.ts'), 'utf8')

assert.match(apiSource, /streamChatQuery:\s*\(/)
assert.match(apiSource, /`\/graph\/chats\/\$\{chatId\}\/queries\/stream`/)
assert.match(apiSource, /record_id\?: number/)
assert.match(answerSource, /streamChatQuery\(\s*_currentChatId\.value/)
assert.match(answerSource, /currentRecord\.id = run\.record_id/)
assert.match(chatApiSource, /execution_type\?: 'legacy' \| 'agentic' \| 'graph'/)
assert.match(indexSource, /answerComponentForRecord/)
assert.match(indexSource, /record\?\.execution_type === 'graph'/)
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd frontend
node --experimental-strip-types tests/graphChatHistory.test.ts
```

Expected: FAIL because the interactive frontend API and execution-type routing do not exist.

- [ ] **Step 3: Add frontend DTO fields and interactive API**

In `chat.ts`:

```typescript
execution_type?: 'legacy' | 'agentic' | 'graph'
```

Assign `record.execution_type = data.execution_type` in `toChatRecord()`.

In `graph-workflow.ts`:

```typescript
export interface GraphRunResponse {
  run_id: string
  record_id?: number
  status: string
  current_node?: string
  output?: Record<string, any>
  context_summary?: {
    question?: string
    dataset_id?: number
    variables?: Record<string, any>
    pending_interaction?: GraphPendingInteraction | null
  }
}

streamChatQuery: (
  chatId: number,
  data: GraphQueryRequest,
  handlers: GraphStreamHandlers = {}
) =>
  streamGraphSse(`/graph/chats/${chatId}/queries/stream`, {
    method: 'POST',
    body: JSON.stringify({ definition_version: 'v1', ...data }),
    handlers,
  }),
```

Remove `chat_id` from the body request interface; the path owns this value.

- [ ] **Step 4: Send through the interactive API and capture record_id**

In `GraphWorkflowAnswer.vue`, add the current-chat computed value and reject a missing ID before starting the stream:

```typescript
const _currentChatId = computed(() => props.currentChatId)
```

Use the interactive call:

```typescript
graphWorkflowApi.streamChatQuery(
  _currentChatId.value,
  {
    run_id: currentRecord.trace_id,
    question: currentRecord.question || '',
    dataset_id: currentDatasetId,
    definition_version: 'v1',
  },
  {
    signal: streamController.value?.signal,
    onEvent: queueLiveEvent,
  }
)
```

Update run application:

```typescript
function applyRunToRecord(run: GraphRunResponse, currentRecord: ChatRecord) {
  if (run.record_id) currentRecord.id = run.record_id
  currentRecord.trace_id = run.run_id
  currentRecord.execution_type = 'graph'
}
```

Insert these three assignments before the existing status, interaction, answer and SQL mapping statements in `applyRunToRecord()`.

Terminal records continue to render their local snapshot. Only `running` and `waiting_input` records call `refreshRun()` during `onMounted()`.

- [ ] **Step 5: Render each history record by its own execution type**

Replace the global component selector with:

```typescript
function answerComponentForRecord(record?: ChatRecord) {
  if (record?.execution_type === 'graph') return GraphWorkflowAnswer
  if (record?.execution_type === 'agentic') return AgenticAnswer
  if (record?.execution_type === 'legacy') return ChartAnswer
  if (useGraphChatFlow.value) return GraphWorkflowAnswer
  return useAgenticChatFlow.value ? AgenticAnswer : ChartAnswer
}
```

Use `:is="answerComponentForRecord(message.record)"`. When creating a new temporary record, set its execution type from the active flow so the first render uses the correct component.

```typescript
currentRecord.execution_type = useGraphChatFlow.value
  ? 'graph'
  : useAgenticChatFlow.value
    ? 'agentic'
    : 'legacy'
```

- [ ] **Step 6: Run frontend contract and display tests**

Run:

```bash
cd frontend
node --experimental-strip-types tests/graphChatHistory.test.ts
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
```

Expected: both commands exit 0.

- [ ] **Step 7: Run frontend typecheck and build**

Run:

```bash
cd frontend
npm run build
```

Expected: vue-tsc and Vite build complete successfully.

- [ ] **Step 8: Commit frontend history recovery**

```bash
git add frontend/src/api/chat.ts frontend/src/api/graph-workflow.ts frontend/src/views/chat/index.vue frontend/src/views/chat/answer/GraphWorkflowAnswer.vue
git add -f frontend/tests/graphChatHistory.test.ts
git commit -m "fix: restore graph chat history after reload"
```

---

### Task 7: Delete Graph workflow data with the owning chat

**Files:**
- Create: `backend/apps/workflow_engine/infrastructure/artifacts/cleanup.py`
- Create: `backend/apps/chat/services/deletion.py`
- Create: `backend/tests/chat/test_graph_chat_deletion.py`
- Modify: `backend/apps/workflow_engine/infrastructure/artifacts/file_store.py`
- Modify: `backend/apps/chat/curd/chat.py:108-128`
- Modify: `backend/apps/chat/api/chat.py:169-184`
- Modify: `backend/main.py:55-90`

**Interfaces:**
- Produces: `workflow_artifact_root() -> Path`
- Produces: `delete_artifact_body(storage_uri: str, root: Path | None = None) -> None`
- Produces: `ArtifactCleanupService.process_pending(max_attempts: int = 3) -> int`
- Produces: `ChatDeletionService.delete_for_user(current_user, chat_id) -> str`

- [ ] **Step 1: Write failing Artifact cleanup and deletion tests**

Add deterministic fixtures to `backend/tests/chat/test_graph_chat_deletion.py`:

```python
@pytest.fixture
def current_user():
    return SimpleNamespace(id=9301, oid=9401)


@pytest.fixture
def session(current_user):
    with Session(engine) as session:
        yield session
        session.rollback()
        owned_run_ids = session.exec(
            select(WorkflowRunModel.run_id).where(WorkflowRunModel.user_id == current_user.id)
        ).all()
        if owned_run_ids:
            session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.in_(owned_run_ids)))
            session.execute(delete(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(owned_run_ids)))
            session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(owned_run_ids)))
        record_ids = session.exec(
            select(ChatRecord.id).where(ChatRecord.create_by == current_user.id)
        ).all()
        if record_ids:
            session.execute(delete(ChatLog).where(ChatLog.pid.in_(record_ids)))
            session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
        session.execute(delete(Chat).where(Chat.create_by == current_user.id))
        session.execute(
            delete(WorkflowArtifactCleanupModel).where(
                WorkflowArtifactCleanupModel.artifact_id.in_([
                    "artifact-delete",
                    "owned-delete-artifact",
                ])
            )
        )
        session.commit()
```

Create tests for safe body deletion:

```python
def test_artifact_cleanup_deletes_body_and_marks_task_succeeded(session, tmp_path):
    body = tmp_path / "artifact-delete.json"
    body.write_text("{}")
    task = WorkflowArtifactCleanupModel(
        artifact_id="artifact-delete",
        storage_uri=body.as_uri(),
        status="pending",
        attempts=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()

    processed = ArtifactCleanupService(session, root=tmp_path).process_pending()

    assert processed == 1
    assert body.exists() is False
    assert task.status == "succeeded"
```

Create a deletion integration test:

```python
def test_chat_deletion_removes_owned_workflow_data_but_keeps_standalone_run(session, current_user):
    now = datetime.now(timezone.utc)
    chat = Chat(
        oid=current_user.oid,
        create_time=now.replace(tzinfo=None),
        create_by=current_user.id,
        brief="delete-owned-chat",
        chat_type="chat",
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
    )
    session.add(chat)
    session.flush()
    record = ChatRecord(
        chat_id=chat.id or 0,
        create_time=now.replace(tzinfo=None),
        create_by=current_user.id,
        dataset_id=20,
        datasource=40,
        engine_type="PostgreSQL",
        execution_type="graph",
        question="删除测试",
        status="succeeded",
        trace_id="owned-delete-run",
        finish=True,
    )
    session.add(record)
    session.flush()
    owned_run = WorkflowRunModel(
        run_id="owned-delete-run",
        oid=current_user.oid,
        user_id=current_user.id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="delete-test",
        status="succeeded",
        chat_id=chat.id,
        record_id=record.id,
        context={},
        request={},
        output={},
        version=1,
        created_at=now,
        updated_at=now,
    )
    standalone = WorkflowRunModel(
        run_id="standalone-keep",
        oid=current_user.oid,
        user_id=current_user.id,
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="delete-test",
        status="succeeded",
        context={},
        request={},
        output={},
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(owned_run)
    session.add(standalone)
    session.add(
        WorkflowEventModel(
            event_id="owned-delete-event",
            run_id=owned_run.run_id,
            sequence=1,
            event_type="run.succeeded",
            public_payload={},
            internal_payload={},
            created_at=now,
        )
    )
    session.commit()

    ChatDeletionService(session).delete_for_user(current_user, chat.id)

    assert session.get(Chat, chat.id) is None
    assert session.get(ChatRecord, record.id) is None
    assert session.exec(
        select(WorkflowRunModel).where(WorkflowRunModel.run_id == owned_run.run_id)
    ).one_or_none() is None
    assert session.exec(
        select(WorkflowRunModel).where(WorkflowRunModel.run_id == standalone.run_id)
    ).one() is not None
```

Also call deletion twice and assert the second call returns the same deleted message without raising.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/chat/test_graph_chat_deletion.py -q
```

Expected: FAIL because the services and safe deletion helpers do not exist.

- [ ] **Step 3: Centralize Artifact root and safe URI deletion**

In `file_store.py`:

```python
def workflow_artifact_root() -> Path:
    """返回 Graph Artifact 正文的唯一根目录。"""

    return Path(
        os.getenv(
            "SQLBOT_WORKFLOW_ARTIFACT_DIR",
            str(Path(__file__).resolve().parents[4] / "data" / "workflow_artifacts"),
        )
    ).expanduser().resolve()


def delete_artifact_body(storage_uri: str, root: Path | None = None) -> None:
    """只允许删除 Artifact 根目录内的 file URI。"""

    effective_root = (root or workflow_artifact_root()).resolve()
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        raise ValueError("ARTIFACT_STORAGE_URI_UNSUPPORTED")
    path = Path(unquote(parsed.path)).resolve()
    if not path.is_relative_to(effective_root):
        raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
    path.unlink(missing_ok=True)
```

Make `FileArtifactStore` and `workflow/runtime.py` use `workflow_artifact_root()` instead of duplicating root resolution.

- [ ] **Step 4: Implement retryable Artifact cleanup service**

Create `cleanup.py`:

```python
class ArtifactCleanupService:
    def __init__(self, session: Session, root: Path | None = None) -> None:
        self._session = session
        self._root = root

    def process_pending(self, max_attempts: int = 3) -> int:
        tasks = self._session.exec(
            select(WorkflowArtifactCleanupModel).where(
                WorkflowArtifactCleanupModel.status.in_(["pending", "failed"]),
                WorkflowArtifactCleanupModel.attempts < max_attempts,
            )
        ).all()
        processed = 0
        for task in tasks:
            task.attempts += 1
            task.updated_at = datetime.now(timezone.utc)
            try:
                delete_artifact_body(task.storage_uri, root=self._root)
            except (OSError, ValueError) as exc:
                task.status = "failed"
                task.last_error = str(exc)
            else:
                task.status = "succeeded"
                task.last_error = None
                processed += 1
            self._session.add(task)
        self._session.commit()
        return processed
```

The exception list is intentionally narrow; do not catch `Exception`.

- [ ] **Step 5: Implement explicit Chat deletion service**

Create `deletion.py` with one readable transaction flow:

```python
class ChatDeletionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_for_user(self, current_user: Any, chat_id: int) -> str:
        chat = self._session.get(Chat, chat_id)
        if chat is None:
            return f"Chat with id {chat_id} has been deleted"
        if chat.create_by != current_user.id:
            raise ValueError(f"Chat with id {chat_id} not Owned by the current user")

        record_ids = list(
            self._session.exec(select(ChatRecord.id).where(ChatRecord.chat_id == chat_id)).all()
        )
        run_ids = list(
            self._session.exec(
                select(WorkflowRunModel.run_id).where(WorkflowRunModel.chat_id == chat_id)
            ).all()
        )
        artifacts = self._session.exec(
            select(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(run_ids))
        ).all() if run_ids else []

        now = datetime.now(timezone.utc)
        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        existing_cleanup_ids = set(
            self._session.exec(
                select(WorkflowArtifactCleanupModel.artifact_id).where(
                    WorkflowArtifactCleanupModel.artifact_id.in_(artifact_ids)
                )
            ).all()
        ) if artifact_ids else set()
        for artifact in artifacts:
            if artifact.artifact_id in existing_cleanup_ids:
                continue
            self._session.add(
                WorkflowArtifactCleanupModel(
                    artifact_id=artifact.artifact_id,
                    storage_uri=artifact.storage_uri,
                    status="pending",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )

        if run_ids:
            self._session.execute(delete(NodeExecutionModel).where(NodeExecutionModel.run_id.in_(run_ids)))
            self._session.execute(delete(WorkflowCheckpointModel).where(WorkflowCheckpointModel.run_id.in_(run_ids)))
            self._session.execute(delete(WorkflowEventModel).where(WorkflowEventModel.run_id.in_(run_ids)))
            self._session.execute(delete(InteractionRequestModel).where(InteractionRequestModel.run_id.in_(run_ids)))
            self._session.execute(delete(WorkflowArtifactModel).where(WorkflowArtifactModel.run_id.in_(run_ids)))
            self._session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(run_ids)))
        if record_ids:
            self._session.execute(delete(ChatLog).where(ChatLog.pid.in_(record_ids)))
            self._session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
        self._session.delete(chat)
        self._session.commit()

        ArtifactCleanupService(self._session).process_pending()
        return f"Chat with id {chat_id} has been deleted"
```

Do not swallow unique constraint failures from concurrent deletion attempts; the API must return the explicit database conflict instead of silently losing a cleanup task.

- [ ] **Step 6: Wire deletion API and startup retry**

Make `delete_chat_with_user()` delegate to `ChatDeletionService`. Keep the API permission and audit decorators unchanged:

```python
def delete_chat_with_user(session, current_user: CurrentUser, chart_id) -> str:
    """删除会话及其关联的 Graph 执行数据。"""

    return ChatDeletionService(session).delete_for_user(current_user, chart_id)
```

In `main.py` add:

```python
def init_workflow_artifact_cleanup() -> None:
    """应用启动时重试上次未完成的 Artifact 正文清理。"""

    with Session(engine) as session:
        ArtifactCleanupService(session).process_pending()
```

Call it after migrations and before logging initialization complete.

- [ ] **Step 7: Run deletion tests**

Run:

```bash
cd backend
uv run pytest tests/chat/test_graph_chat_deletion.py tests/workflow_engine/test_file_artifact_store.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit lifecycle deletion**

```bash
git add backend/apps/workflow_engine/infrastructure/artifacts/cleanup.py backend/apps/workflow_engine/infrastructure/artifacts/file_store.py backend/apps/chat/services/deletion.py backend/apps/chat/curd/chat.py backend/apps/chat/api/chat.py backend/main.py
git add -f backend/tests/chat/test_graph_chat_deletion.py
git commit -m "feat: clean graph workflow data with chat deletion"
```

---

### Task 8: Add end-to-end regression coverage and run the full verification gate

**Files:**
- Modify: `backend/tests/workflow_engine/test_graph_api.py`
- Modify: `frontend/tests/graphChatHistory.test.ts`
- Modify: `docs/superpowers/specs/2026-07-10-chatbi-graph-history-persistence-design.md` only if implementation reveals a genuine contract correction

**Interfaces:**
- Consumes: all previous tasks
- Produces: executable regression coverage for success, refresh, clarification and deletion

- [ ] **Step 1: Write end-to-end success-history test**

Create a test that exercises the interactive API and direct history read:

```python
def test_graph_chat_history_survives_reload_boundary():
    chat_id, dataset_id = _seed_graph_chat()
    response = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-e2e-history-success",
        },
    )
    assert response.status_code == 200
    record_id = response.json()["record_id"]

    with Session(engine) as session:
        record = session.get(ChatRecord, record_id)
        assert record.execution_type == "graph"
        assert record.status == "succeeded"
        assert record.finish is True
        assert record.sql_answer
        assert record.trace_id == "api-e2e-history-success"
```

- [ ] **Step 2: Write end-to-end clarification recovery test**

```python
def test_graph_chat_waiting_record_resumes_into_stable_snapshot():
    chat_id, dataset_id = _seed_graph_chat()
    created = _client().post(
        f"/graph/chats/{chat_id}/queries",
        json={
            "question": "需要澄清 今日访问人数",
            "dataset_id": dataset_id,
            "definition_version": "v1",
            "run_id": "api-e2e-history-waiting",
        },
    )
    record_id = created.json()["record_id"]
    assert _load_chat_record(record_id).status == "waiting_input"

    interaction_id = _load_pending_interaction_id("api-e2e-history-waiting")
    resumed = _client().post(
        f"/graph/runs/api-e2e-history-waiting/interactions/{interaction_id}/responses",
        json={"response": {"metric": "visit_uv"}},
    )

    assert resumed.status_code == 200
    record = _load_chat_record(record_id)
    assert record.status == "succeeded"
    assert record.finish is True
    assert record.sql_answer
```

- [ ] **Step 3: Run the new end-to-end tests first**

Run:

```bash
cd backend
uv run pytest tests/workflow_engine/test_graph_api.py -k "history_survives_reload_boundary or waiting_record_resumes" -q
```

Expected: PASS.

- [ ] **Step 4: Run backend formatting and focused regression suite**

Run:

```bash
cd backend
uv run ruff check apps/chat apps/agentic_chat apps/workflow_engine apps/workflow tests/chat tests/workflow_engine
uv run pytest tests/chat tests/workflow_engine tests/workflow -q
```

Expected: Ruff exits 0; pytest reports zero failures.

- [ ] **Step 5: Run migration verification**

Run:

```bash
cd backend
uv run alembic upgrade head
uv run alembic current
```

Expected: current revision is `078_graph_chat_history`.

- [ ] **Step 6: Run frontend regression and production build**

Run:

```bash
cd frontend
node --experimental-strip-types tests/graphChatHistory.test.ts
node --experimental-strip-types --test src/views/chat/execution-component/graphWorkflowDisplay.test.mjs
npm run build
```

Expected: both Node tests exit 0 and Vite build succeeds.

- [ ] **Step 7: Verify no unrelated files are staged**

Run:

```bash
git status --short
git diff --check
```

Expected: only intended source, migration and test files are modified; `.dev-logs/*.pid` remain unstaged.

- [ ] **Step 8: Commit end-to-end coverage**

```bash
git add -f backend/tests/workflow_engine/test_graph_api.py frontend/tests/graphChatHistory.test.ts
git add docs/superpowers/specs/2026-07-10-chatbi-graph-history-persistence-design.md
git commit -m "test: cover graph chat history lifecycle"
```

If the design document did not change, omit it from `git add` rather than creating an empty diff.

---

## Final Verification Checklist

- [ ] Interactive Graph routes require a path `chat_id` and return `record_id`.
- [ ] Standalone Graph routes create no ChatRecord and leave Run ownership columns null.
- [ ] Every linked Run save projects ChatRecord in the same Session before event commit.
- [ ] Success snapshots contain a displayable answer; missing answers fail explicitly.
- [ ] Resume, cancel and retry reuse the same ChatRecord.
- [ ] History API returns `execution_type`, `status`, `trace_id`, answer and SQL.
- [ ] Frontend renders old records by `execution_type`, not current global flow flags.
- [ ] Waiting interactions survive page reload and resume into a terminal snapshot.
- [ ] Chat deletion removes linked Workflow rows and schedules Artifact body deletion.
- [ ] Existing orphan Run rows remain untouched.
- [ ] Backend Ruff, backend tests, migration upgrade, frontend contract tests and frontend build all pass.
