# Graph 与 Agentic 能力重叠分析及解耦方案

**定位：** 分析 Graph 主线（`apps/workflow`）的能力层与 Agentic ChatBI（规划中的 `apps/agent`，含冻结的 `apps/agentic_chat` v1）在工具/能力上的重叠，给出代码结构解耦与规整方案。
**分析方法：** 基于当前代码实测的 import 依赖与逐文件核对，非文档推断。
**关联文档：** [Agentic-ChatBI技术文档.md](./Agentic-ChatBI技术文档.md)、[09-agentic-chatbi-tech-design.md](./09-agentic-chatbi-tech-design.md)、[08-graph-workflow-runtime-tech-design.md](./08-graph-workflow-runtime-tech-design.md)
**日期：** 2026-07-10

---

## 1. 现状依赖关系（代码实测）

```text
apps/api.py ──────────────> agentic_chat.api          # v1 入口仍挂载

workflow（Graph 主线）
  ├─ runtime.py ──────────────────> agentic_chat.tools.SqlExecuteTool
  ├─ capabilities/execution.py ───> agentic_chat.{ToolResult, SqlExecuteTool}
  │                                 + workflow_engine.domain.artifact.ArtifactRef
  ├─ capabilities/adapters/permission.py ─> agentic_chat.{ToolResult, PermissionTool}
  ├─ capabilities/adapters/sql.py ────────> agentic_chat.{SQLRepairStrategy,
  │                                          SqlExecuteTool, SqlValidateTool}
  │                                          + headless.{SchemaBuilder, SemanticSQLCompiler}
  └─ capabilities/adapters/knowledge.py ──> headless.*（SchemaMapper/MetricEmbedding/AssetDocument）

agentic_chat（v1，冻结）──> headless / datasource / db   # 无反向依赖 workflow ✅
```

三个结构性事实：

1. **Graph 主线反向依赖冻结的 v1 包**。`workflow` 有 4 个文件 import `apps.agentic_chat`，依赖五件套：`ToolResult`（结果信封）、`SqlExecuteTool`、`SqlValidateTool`、`PermissionTool`、`SQLRepairStrategy`。09 设计承诺"v2 评估完成后删除 v1"，但按当前代码删 v1 会直接打断 Graph 主线——**这五件套实际是共享领域能力，寄居在了错误的包里**。
2. **adapters 对图上下文存在隐式耦合**。09 设计的复用契约是"adapters 不依赖图运行时（入参是普通领域对象），可安全被 agentic 工具层 import"。实测只对了一半：adapters 确实不 import `workflow_engine`（除 `execution.py` 依赖 `ArtifactRef`），但 `knowledge.py`/`sql.py` 的入参是 `ChatBIRunContext(request)`——即图节点组装的完整上下文视图（request / conversation / variables / inputs / node_name），内部读取的是图状态特有的 intent/slot/query_plan 结构。**agentic v2 若直接调用，必须伪造一份图节点上下文字典**——这是比显式 import 更隐蔽的耦合。
3. **`capabilities/execution.py` 混装了两种东西**：领域逻辑（执行输出构造、采样、校验）与图运行时概念（`ArtifactRef` 工件存储协议），导致整个 capabilities 包无法脱离 `workflow_engine` 复用。

## 2. 重叠矩阵：Agentic v2 九工具 × 现有实现

| v2 工具 | 现有重叠实现 | 真正的领域逻辑在哪 | 复用障碍 |
| --- | --- | --- | --- |
| `search_semantic_assets` | `adapters/knowledge.py`（1893 行）：`SemanticKnowledgeAdapter.retrieve` + `CandidateGate` + `SemanticDocumentRetriever` | 检索/门控/重排核心在 knowledge.py 内部，底座在 `apps.semantic` | 入参是 `ChatBIRunContext`，读取图特有的 intent/slot 结构；返回结构面向图节点 output_path |
| `get_dataset_schema` | `headless.service.SemanticSchemaBuilder`（语义层）；v1 `SchemaTool`（走 CoreTable/CoreField，另一套） | `apps.semantic` | 无障碍（headless 是干净的）；但存在 headless 与 datasource 两套 schema 读取并行 |
| `compile_semantic_sql` | ① `adapters/sql.py` `SqlAdapter.generate`；② v1 `SemanticSQLCompilerTool` | 编译器本体在 `headless.sql_compiler`（干净）；**但"数据集解析 + 编译请求组装"逻辑在 ①② 各写了一份** | 已经出现两份重复；v2 若再写第三份则三处分叉 |
| `validate_sql` | v1 `SqlValidateTool`（82 行，sqlparse 只读校验/auto-limit） | `apps.agentic_chat`（寄居错位） | Graph 也依赖它，包归属错误 |
| `execute_sql` | ① v1 `SqlExecuteTool`（薄封装 exec_sql）；② `capabilities/execution.py`（输出构造/采样/校验）；③ `SqlAdapter.execute/execute_split`（权限→校验→执行→修复链、并行执行、工件落盘） | 执行守护链分散在 v1 + capabilities 两个包三处 | ③ 耦合 `ChatBIRunContext` 与 `ArtifactRef`；②③ 中的采样/截断/守护正是 v2 需要的 |
| `clarify` | `adapters/interaction.py`（`ClarificationPlan`/`ClarificationCardBuilder`）+ v1 `AgenticClarification` 模型 | 澄清选项/卡片结构可共享 | 卡片构造面向图交互节点的 schema |
| `finish` | `adapters/answer.py`（答案 prompt + 结果投影）+ `apps.template.generate_chart` | 图表模板在 `apps.template`（干净）；结果投影逻辑可共享 | answer prompt 面向图节点上下文 |
| `search_terminology` | v1 `TerminologyTool` 是空壳；真实检索在 `apps.terminology` | `apps.terminology` | 无障碍 |
| `get_sql_examples` | v1 `SqlExampleTool` 是空壳；真实检索在 `apps.data_training` | `apps.data_training` | 无障碍 |

**结论：九个工具中有六个（semantic/compile/validate/execute/clarify/finish）与 Graph 能力层重叠，且重叠部分的领域逻辑当前分散在三个包（`agentic_chat`、`workflow.capabilities`、`apps.semantic`），只有 headless 一层是干净可直接复用的。**

不重叠的部分（Graph 专属，v2 不需要）：`adapters/question.py`（2104 行，分类/改写/意图识别的 prompt 编排——v2 由 LLM 在循环内自主完成理解）、`capabilities/planning.py`（查询计划绑定）、`capability_matrix.py`（能力路由决策）。这些是图架构"把理解拆成节点"的产物，恰好是 agentic 架构用模型规划权替代掉的东西，**不应下沉、不应共享**。

## 3. 问题定性

| # | 问题 | severity | 影响 |
| --- | --- | --- | --- |
| P0-1 | Graph 主线 import 冻结待删的 v1 包（五件套） | 高 | v1 删不掉；"冻结"名存实亡——改 v1 工具会波及 Graph |
| P0-2 | `ToolResult` 作为事实上的共享结果信封，定义在 v1 包里 | 高 | 所有能力层的公共类型寄居在待删包 |
| P1-1 | adapters 复用契约与实际不符（`ChatBIRunContext` 隐式耦合） | 高 | v2 按 09 设计"直接 import adapters"不可行，会被迫伪造图上下文或复制逻辑 |
| P1-2 | 数据集解析 + 编译请求组装已有两份重复（SqlAdapter vs v1 工具） | 中 | v2 落地时若不收敛将变三份 |
| P1-3 | `execution.py` 领域逻辑与 `ArtifactRef` 运行时概念混装 | 中 | 采样/截断/输出构造无法被 v2 复用 |
| P2-1 | schema 读取双轨（headless 语义 schema vs CoreTable 物理 schema） | 低 | 两套并行是[有意设计](../../docs/tech/07-headless-semantic-asset-storage-gap-tech-design.md)，但工具层需明确各自入口 |

## 4. 解耦方案：建立共享能力层 `apps/capabilities`

### 4.1 目标依赖规则

```text
                 ┌────────────────────┐        ┌──────────────────┐
                 │  workflow    │        │  agent    │
                 │  (Graph 主线)       │        │  (Agentic v2)    │
                 │  adapters = 防腐层  │        │  tools = 防腐层  │
                 └─────────┬──────────┘        └────────┬─────────┘
                           │      依赖方向唯一向下       │
                           ▼                            ▼
                 ┌─────────────────────────────────────────────┐
                 │        apps/capabilities              │
                 │  领域能力层：纯领域对象入参，无 runtime 依赖  │
                 └─────────┬───────────────────────────────────┘
                           ▼
        apps/semantic  ·  apps/terminology  ·  apps/data_training
        apps/datasource  ·  apps/db  ·  apps/template
```

硬性规则（可用 import-linter 之类约束固化）：

- `capabilities` **禁止** import `workflow_engine` / `workflow` / `agent` / `agentic_chat`。
- `workflow` 与 `agent` **禁止**互相 import，**禁止** import `agentic_chat`。
- 两侧各自的"防腐层"职责：Graph 的 adapter 负责 `ChatBIRunContext` → 领域参数的翻译；Agentic 的 tool 负责 pydantic tool-schema → 领域参数的翻译 + `summary`/`payload` 分离。**翻译层不含领域逻辑。**

### 4.2 能力层内容规划

| 模块 | 内容 | 来源 |
| --- | --- | --- |
| `schemas.py` | `ToolResult`/`CapabilityResult` 通用结果信封 | ← `agentic_chat/schemas.py`（平移） |
| `sql/validator.py` | 只读校验、auto-limit、越权对象检查 | ← `agentic_chat/tools/sql_validator.py`（平移） |
| `sql/executor.py` | 执行 + 采样/行数/字节截断 + 输出构造 | ← `agentic_chat/tools/sql_executor.py` + `capabilities/execution.py` 的领域部分（合并） |
| `sql/permission.py` | 行列权限改写（当前透传占位，未来实现落这里） | ← `agentic_chat/tools/permission.py`（平移） |
| `sql/repair.py` | SQL 修复策略 | ← `agentic_chat/strategies/sql_repair.py`（平移） |
| `sql/llm_generate.py` | 受约束 LLM 生成 SQL 核心（prompt 构造 + 生成调用 + 校验反馈重试环），Graph 的 constrained_llm 通道与 Agentic 的自由生成兜底路径共用 | 新建（设计见 [chatbi-v1-vector-retrieval-llm-sql-design.md](../chatbi-v1-vector-retrieval-llm-sql-design.md) 方案 B） |
| `semantic/retrieval.py` | 语义资产检索核心：`CandidateGate` + 检索/门控/重排主体，入参改为显式领域请求（question、dataset_id、intent 摘要、top-k） | ← `adapters/knowledge.py` 抽取（重构，最大工作量项） |
| `semantic/compile.py` | 数据集解析 + `SemanticSQLCompileRequest` 组装（收敛现有两份重复） | ← `SqlAdapter.generate` 与 v1 `SemanticSQLCompilerTool` 合并 |
| `interaction/clarification.py` | 澄清选项/卡片的领域结构（`ClarificationPlan`、option schema） | ← `adapters/interaction.py` 抽取结构部分 |

**不下沉**（留在 Graph 侧）：`question.py` 全部 prompt 编排、`planning.py`、`capability_matrix.py`、`ArtifactRef` 工件存储协议、`ChatBIRunContext`/`ChatBIConfig` 中图流程特有的配置项。

### 4.3 为什么是独立包而不是塞进 `apps/semantic`

`headless` 的边界是**语义资产的定义、存储与编译**（模型/指标/维度/数据集/编译器），保持它只做语义层是它可复用的前提。SQL 执行守护、权限改写、澄清结构、结果采样都不是语义层概念，塞进去会让 headless 变成杂物间。检索核心（`semantic/retrieval.py`）虽然贴近 headless，但它包含检索策略与门控阈值这类**问数流程决策**，与 headless 的"资产事实"有本质区别，放能力层更合适。

## 5. 迁移步骤（小步、每步可独立合入回归）

| 步骤 | 内容 | 效果 | 风险 |
| --- | --- | --- | --- |
| **Step 1：平移五件套** | 新建 `apps/capabilities`，把 `ToolResult`/validator/executor/permission/repair 原样搬入；`workflow` 4 个文件与 `agentic_chat` 改 import（v1 内部可留兼容 re-export） | 解除 Graph→v1 依赖，v1 恢复"可删"状态 | 极低（纯搬家零逻辑改动，现有测试即回归） |
| **Step 2：execution 拆分** | `capabilities/execution.py` 的输出构造/采样/校验下沉到 `sql/executor.py`；`ArtifactRef` 存储协议留在 Graph 侧薄壳 | 能力层不再触碰 `workflow_engine` | 低 |
| **Step 3：compile 收敛** | `SqlAdapter` 与 v1 工具中两份"数据集解析+编译请求组装"合并为 `semantic/compile.py`，两处调用方改为薄翻译 | 消除既有重复，v2 `compile_semantic_sql` 有唯一实现可调 | 中（两份实现存在行为差异，需对齐测试） |
| **Step 4：检索核心解耦** | `knowledge.py` 的 `retrieve` 主体与 `ChatBIRunContext` 解耦：入参改显式领域请求；Graph adapter 只做 ctx→参数翻译 | v2 `search_semantic_assets` 可直接调用；1893 行大文件顺势瘦身 | 中高（knowledge.py 是 Graph 正确率的核心路径，需评测集回归） |
| **Step 5：v2 落地** | `apps/agent` 工具层只 import `capabilities`（+ headless/terminology/data_training），全程不 import `workflow` | 两条链路真正并行、互不侵入 | — |

Step 1–2 建议在 v2 开工前完成（它们同时是 Graph 自身的债务清理）；Step 3–4 可与 v2 P0 并行推进，v2 P0 期间 `search_semantic_assets` 可临时用翻译垫片调用现有 adapter，Step 4 完成后切换。

## 6. 对已有设计文档的修订点

- 09 设计 §10 与《Agentic-ChatBI技术文档》§4.5 中"工具层优先复用 `workflow/capabilities/adapters/*`，adapters 不依赖图运行时、可安全 import"的表述**不成立**（见第 1 节事实 2），且其中预留的"若产生耦合则下沉到独立 `apps/capabilities` 包"的触发条件**现在就已满足**——耦合不是未来风险，是既成事实。复用策略应修订为：**复用共享能力层 `capabilities`，而非 Graph 的 adapters**。
- 09 设计 §10 "v2 评估完成后删除 v1"需补充前置条件：Step 1 平移完成前 v1 不可删。
