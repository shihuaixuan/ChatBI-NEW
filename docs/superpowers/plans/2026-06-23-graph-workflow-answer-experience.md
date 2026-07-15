# Graph Workflow Answer Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw Graph Workflow JSON output with a user-facing progress, clarification, final answer, and technical-detail experience.

**Architecture:** Keep `GraphWorkflowAnswer.vue` as the run orchestration component. Add a pure display helper for trace/interaction mapping, then render it through focused Vue components: progress, interaction card, final answer, and collapsed technical trace. Existing backend APIs are reused; no runtime contract change is required for the first pass.

**Tech Stack:** Vue 3, TypeScript, Element Plus, Vite, Node built-in assertions for helper tests, `npm run build`.

---

## File Structure

- Create `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts`: pure mapping functions for trace steps, progress headlines, interaction options, and interaction response payloads.
- Create `frontend/tests/graphWorkflowDisplay.test.ts`: Node-runnable TypeScript tests for the pure mapping functions.
- Create `frontend/src/views/chat/execution-component/GraphWorkflowProgress.vue`: visible progress summary and expandable business-step list.
- Create `frontend/src/views/chat/execution-component/GraphWorkflowInteractionCard.vue`: Graph-specific business clarification card.
- Create `frontend/src/views/chat/execution-component/GraphWorkflowFinalAnswer.vue`: final natural-language answer area.
- Create `frontend/src/views/chat/execution-component/GraphWorkflowTechnicalTrace.vue`: collapsed raw trace/debug details.
- Modify `frontend/src/views/chat/execution-component/GraphWorkflowTrace.vue`: fetch trace and compose progress + technical trace.
- Modify `frontend/src/views/chat/answer/GraphWorkflowAnswer.vue`: stop rendering Agentic `ClarificationCard`, render Graph-specific interaction card and final answer.
- Modify `frontend/src/views/chat/index.vue`: ensure Graph workflow component remains selected when `VITE_GRAPH_CHATBI_ENABLED=true`.

---

### Task 1: Add Failing Tests For Graph Display Mapping

**Files:**
- Create: `frontend/tests/graphWorkflowDisplay.test.ts`
- Create later: `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/graphWorkflowDisplay.test.ts`:

```ts
import assert from 'node:assert/strict'

import {
  buildGraphInteractionResponse,
  buildGraphWorkflowSteps,
  graphProgressHeadline,
  normalizeGraphInteraction,
} from '../src/views/chat/execution-component/graphWorkflowDisplay'

const waitingInteraction = {
  interaction_id: 'interaction-1',
  run_id: 'run-1',
  node_name: 'ask_metric_selection',
  status: 'pending',
  prompt: '请选择要分析的指标。',
  response_schema: {
    type: 'object',
    properties: {
      metric: { type: 'string' },
      skipped: { type: 'boolean' },
    },
  },
  options: [
    { label: '访问人数', value: 11 },
    { label: '访客数', value: 12 },
  ],
}

const waitingTrace = {
  run_id: 'run-1',
  status: 'waiting_input',
  current_node: 'ask_metric_selection',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    { name: 'retrieve_knowledge', status: 'succeeded', output: { selected_assets: { metrics: [{ name: '访问人数' }] } } },
    { name: 'ask_metric_selection', status: 'waiting_input', output: { prompt: '请选择要分析的指标。' } },
  ],
}

const succeededTrace = {
  run_id: 'run-2',
  status: 'succeeded',
  current_node: 'finish',
  nodes: [
    { name: 'classify_question', status: 'succeeded', output: { category: 'data' } },
    { name: 'generate_sql', status: 'succeeded', output: { sql: 'select 1' } },
    { name: 'execute_sql', status: 'succeeded', output: { row_count: 1 } },
    { name: 'compose_final_reply', status: 'succeeded', output: { answer: '今天访问人数为 100。' } },
  ],
}

const waitingSteps = buildGraphWorkflowSteps(waitingTrace, waitingInteraction)
assert.equal(waitingSteps.at(-1)?.label, '选择分析指标')
assert.equal(waitingSteps.at(-1)?.status, 'waiting_input')
assert.equal(waitingSteps.at(-1)?.summary, '请选择要分析的指标。')
assert.equal(graphProgressHeadline(waitingTrace, waitingInteraction), '需要你补充信息：请选择要分析的指标。')

const normalized = normalizeGraphInteraction(waitingInteraction)
assert.equal(normalized.title, '请选择要分析的指标')
assert.equal(normalized.targetSlots[0], 'metric')
assert.deepEqual(
  buildGraphInteractionResponse(waitingInteraction, normalized.options[0]),
  { metric: '11' }
)
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '客流人数'), {
  metric: '客流人数',
})
assert.deepEqual(buildGraphInteractionResponse(waitingInteraction, undefined, '', true), {
  skipped: true,
})

const doneSteps = buildGraphWorkflowSteps(succeededTrace, null)
assert.equal(graphProgressHeadline(succeededTrace, null), '已完成')
assert.equal(doneSteps.at(-1)?.label, '整理回复')
assert.equal(doneSteps.at(-1)?.status, 'succeeded')
assert.equal(doneSteps.find((step) => step.key === 'execute_sql')?.summary, '返回 1 行')

console.info('graphWorkflowDisplay tests passed')
```

- [ ] **Step 2: Verify the tests fail because the helper does not exist**

Run from `frontend/`:

```bash
rm -rf /tmp/sqlbot-gw-display-tests
./node_modules/.bin/tsc --target ES2022 --module commonjs --moduleResolution node --skipLibCheck --strict --outDir /tmp/sqlbot-gw-display-tests src/views/chat/execution-component/graphWorkflowDisplay.ts tests/graphWorkflowDisplay.test.ts
node /tmp/sqlbot-gw-display-tests/tests/graphWorkflowDisplay.test.js
```

Expected: FAIL with `File 'src/views/chat/execution-component/graphWorkflowDisplay.ts' not found`.

---

### Task 2: Implement Graph Display Helper

**Files:**
- Create: `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts`
- Test: `frontend/tests/graphWorkflowDisplay.test.ts`

- [ ] **Step 1: Implement the pure helper**

Create `frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts` with:

```ts
export type GraphStepStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'waiting_input'
  | 'skipped'
  | 'cancelled'

export interface GraphTraceNodeLike {
  name: string
  status: string
  route_reason?: string
  output?: any
}

export interface GraphTraceLike {
  run_id: string
  status: string
  current_node?: string
  nodes: GraphTraceNodeLike[]
}

export interface GraphPendingInteractionLike {
  interaction_id: string
  run_id: string
  node_name: string
  status: string
  prompt?: string
  options?: Array<Record<string, any>>
  response_schema?: Record<string, any>
}

export interface GraphWorkflowStep {
  key: string
  label: string
  status: GraphStepStatus
  summary: string
}

export interface NormalizedGraphInteractionOption {
  label: string
  value: any
  slot: string
  raw: Record<string, any>
}

export interface NormalizedGraphInteraction {
  title: string
  prompt: string
  targetSlots: string[]
  options: NormalizedGraphInteractionOption[]
}
```

Then implement:

- `buildGraphWorkflowSteps(trace, pendingInteraction)`
- `graphProgressHeadline(trace, pendingInteraction)`
- `normalizeGraphInteraction(interaction)`
- `buildGraphInteractionResponse(interaction, option, customValue, skipped)`

Use these labels:

```ts
const NODE_LABELS: Record<string, string> = {
  classify_question: '理解问题',
  rewrite_question: '整理问题',
  ask_rewrite_clarification: '补充问题信息',
  recognize_intent: '识别分析意图',
  ask_intent_clarification: '确认分析方式',
  retrieve_knowledge: '匹配数据资产',
  ask_metric_selection: '选择分析指标',
  generate_sql: '生成查询',
  execute_sql: '查询数据',
  handle_sql_error: '修复查询',
  generate_question_answer: '生成答案',
  recommend_questions: '推荐追问',
  compose_final_reply: '整理回复',
  finish: '结束',
}
```

- [ ] **Step 2: Run helper tests**

Run from `frontend/`:

```bash
rm -rf /tmp/sqlbot-gw-display-tests
./node_modules/.bin/tsc --target ES2022 --module commonjs --moduleResolution node --skipLibCheck --strict --outDir /tmp/sqlbot-gw-display-tests src/views/chat/execution-component/graphWorkflowDisplay.ts tests/graphWorkflowDisplay.test.ts
node /tmp/sqlbot-gw-display-tests/tests/graphWorkflowDisplay.test.js
```

Expected: PASS and output `graphWorkflowDisplay tests passed`.

---

### Task 3: Add User-Facing Progress, Interaction, Final Answer, And Technical Trace Components

**Files:**
- Create: `frontend/src/views/chat/execution-component/GraphWorkflowProgress.vue`
- Create: `frontend/src/views/chat/execution-component/GraphWorkflowInteractionCard.vue`
- Create: `frontend/src/views/chat/execution-component/GraphWorkflowFinalAnswer.vue`
- Create: `frontend/src/views/chat/execution-component/GraphWorkflowTechnicalTrace.vue`
- Modify: `frontend/src/views/chat/execution-component/GraphWorkflowTrace.vue`

- [ ] **Step 1: Add `GraphWorkflowProgress.vue`**

Implement a light component that receives `trace`, `pendingInteraction`, and `loading`, calls `buildGraphWorkflowSteps()` and `graphProgressHeadline()`, and renders:

- one visible headline line,
- compact step chips,
- expandable step details.

- [ ] **Step 2: Add `GraphWorkflowInteractionCard.vue`**

Implement a Graph-specific interaction card:

- uses `normalizeGraphInteraction()`,
- renders options as selectable buttons,
- includes manual input “其他，请补充”,
- emits `submit(response)` from `buildGraphInteractionResponse()`,
- emits `skip()` with `{ skipped: true }`.

- [ ] **Step 3: Add `GraphWorkflowFinalAnswer.vue`**

Render `record.chart_answer || record.sql_answer` through `MdComponent`. If no answer exists and the run is not waiting for input, show a quiet placeholder “正在整理答案...”.

- [ ] **Step 4: Add `GraphWorkflowTechnicalTrace.vue`**

Render a collapsed details panel with raw node output, route reason, run id, and status. The panel must be closed by default.

- [ ] **Step 5: Rework `GraphWorkflowTrace.vue`**

Keep the existing trace fetch logic, but render:

```vue
<GraphWorkflowProgress
  :trace="trace"
  :pending-interaction="pendingInteraction"
  :loading="loading"
  @refresh="loadTrace"
/>
<GraphWorkflowTechnicalTrace :trace="trace" />
```

Remove the always-visible dark JSON panel.

---

### Task 4: Wire Graph Components Into The Answer Flow

**Files:**
- Modify: `frontend/src/views/chat/answer/GraphWorkflowAnswer.vue`

- [ ] **Step 1: Store the Graph pending interaction directly**

In `applyRunToRecord`, set:

```ts
currentRecord.clarification = run.context_summary?.pending_interaction || undefined
```

Do not convert it to `AgenticClarification`.

- [ ] **Step 2: Render Graph interaction card**

Replace `ClarificationCard` with:

```vue
<GraphWorkflowInteractionCard
  :interaction="message.record?.clarification"
  :disabled="_loading"
  @submit="submitInteraction"
  @skip="skipInteraction"
/>
```

- [ ] **Step 3: Submit and skip interactions**

Replace `submitClarification()` with:

```ts
async function submitInteraction(response: Record<string, any>) {
  if (index.value < 0) return
  const currentRecord = _currentChat.value.records[index.value]
  const interactionId = currentRecord.clarification?.interaction_id
  if (!currentRecord.trace_id || !interactionId) return
  _loading.value = true
  try {
    await graphWorkflowApi.answerInteraction(currentRecord.trace_id, String(interactionId), response)
    currentRecord.clarification = undefined
    await refreshRun(currentRecord)
  } finally {
    _loading.value = false
  }
}

function skipInteraction() {
  submitInteraction({ skipped: true })
}
```

- [ ] **Step 4: Render final answer and user-facing trace**

Render:

```vue
<GraphWorkflowFinalAnswer :record="message.record" :loading="_loading" />
<GraphWorkflowTrace
  :run-id="message.record?.trace_id"
  :refresh-key="traceRefreshKey"
  :pending-interaction="message.record?.clarification"
/>
```

Pass `[]` to `BaseAnswer` reasoning names so final answers do not appear under “思考过程”.

---

### Task 5: Verify And Commit

**Files:**
- All files above

- [ ] **Step 1: Run helper tests**

Run from `frontend/`:

```bash
rm -rf /tmp/sqlbot-gw-display-tests
./node_modules/.bin/tsc --target ES2022 --module commonjs --moduleResolution node --skipLibCheck --strict --outDir /tmp/sqlbot-gw-display-tests src/views/chat/execution-component/graphWorkflowDisplay.ts tests/graphWorkflowDisplay.test.ts
node /tmp/sqlbot-gw-display-tests/tests/graphWorkflowDisplay.test.js
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run from `frontend/`:

```bash
npm run build
```

Expected: PASS, allowing existing Vite chunk-size warnings.

- [ ] **Step 3: Inspect staged scope**

Run:

```bash
git status --short
git diff --stat
```

Stage only Graph Workflow answer experience files and the plan:

```bash
git add -f docs/superpowers/plans/2026-06-23-graph-workflow-answer-experience.md \
  frontend/tests/graphWorkflowDisplay.test.ts \
  frontend/src/api/graph-workflow.ts \
  frontend/src/views/chat/answer/GraphWorkflowAnswer.vue \
  frontend/src/views/chat/execution-component/graphWorkflowDisplay.ts \
  frontend/src/views/chat/execution-component/GraphWorkflowProgress.vue \
  frontend/src/views/chat/execution-component/GraphWorkflowInteractionCard.vue \
  frontend/src/views/chat/execution-component/GraphWorkflowFinalAnswer.vue \
  frontend/src/views/chat/execution-component/GraphWorkflowTechnicalTrace.vue \
  frontend/src/views/chat/execution-component/GraphWorkflowTrace.vue
```

If `frontend/src/views/chat/index.vue` contains only the Graph workflow answer-component selection hunk needed for this feature, stage that hunk separately.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: improve graph workflow answer experience"
```

---

## Self-Review

- Spec coverage: The plan covers progress visibility, `waiting_input` business clarification, final answer display, and collapsed technical details.
- Placeholder scan: No placeholder tasks remain; each task has concrete files and verification commands.
- Type consistency: The helper uses local `GraphTraceLike` / `GraphPendingInteractionLike` interfaces so Node tests can compile without Vite aliases; Vue components consume the existing API shapes structurally.
