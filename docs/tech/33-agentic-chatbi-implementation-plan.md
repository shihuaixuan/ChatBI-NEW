# 33. 智能问数架构重设计——P0–P3 分阶段实施计划

> 状态：计划评审稿 v1 ｜ 日期：2026-08-15 ｜ 依据：docs/tech/32（架构方案）
> 性质：**纯计划文档，不包含任何代码变更**。所有文件路径基于 2026-08-15 `codex/headless-dataset-chat` 工作区核实；执行时若结构已变，按 doc 32 的目标语义顺延。
> 迁移基线：alembic 当前最新为 `109_chatbi_memory_recall_variant.py`，新迁移自 **110** 起分配（执行时按当时最新顺延）。
> 变更记录：v1.1（2026-08-16）——按团队决策，**评测平台建设下调为低优先级**：`apps/evaluation` 全套（评测三表/CI 门禁/badcase 队列）从 P0 移至 **P2-3** 与运营闭环合并建设；P0–P1 阶段验收改用现有脚本人工跑批（见 §2 P0-8′、§6）。
> 实施进度（2026-08-16）：P0-1～P0-10 代码、专项测试、50 题 JSONL、真实 PostgreSQL 迁移与文档同步已完成。针对真实跑批失败，已修复 Trace 参数、并发时间结果回投影、无指标明细查询的默认时间维度绑定、排名明细形态校验、模型 JSON 一次受控修复、跨模型不兼容组合正常拒答，以及评测脚本擅自代答澄清的问题。最终 43/50 `finished`、7/50 `waiting_user`、30/50 执行 SQL、43/50 产生答案、`run.failed=0`；按 `expected_points` 人工初审 40/50（80%），P0 的“严格正确率 ≥65%、死局类失败 = 0”已通过。迁移 110 已完成回退/升级及 verified query 创建、认证、废弃、清理闭环验证。P1-1 至 P1-10 已完成首版：除计划/结果集、三模式路由、DuckDB ComputeEngine、意图与语义编译扩展、AnswerComposer、置信度路由、ASSISTED 兜底、多轮 patch、记忆灰度、OTEL metrics 和 retrieval trace 外，已新增数据集级 instructions 资产、Schema 投影、理解/规划提示词固定槽位及 STRICT 推广报告。当前已补齐 STRICT 多查询的多计划保存、时间回投影和 PLAN 按 QueryTask 选择严格计划的执行接线，仍需真实 CROSS_MODEL 评测验证。默认仍为 `react_legacy`，FAST/PLAN 需通过 `CHAT_AGENT_EXECUTION_MODES` 灰度启用；legacy 的 `AgentFinalizationService` 保留为过渡适配，不改变默认旧链路。复杂时间偏移由编译器明确标记为双 QueryTask 路径；RESEARCH 等待 P2-1。剩余 6 条业务结果错误和 4 条多余澄清继续作为质量改进项。

---

## 0. 使用说明与约定

**图例**：`[新]` 新增文件/目录 ｜ `[改]` 修改现有文件 ｜ `[删]` 删除 ｜ `[迁]` alembic 迁移 ｜ `[测]` 测试 ｜ `[前]` 前端配合项（需前端 owner 排期）｜ `[评]` 需新增评测题

**每个工作项的完成定义（DoD）**：代码 + 单元/集成测试 + 对应评测题入库（若标 `[评]`）+ 受影响文档同步（§8）+ 特性开关可回退。

**通用策略**：
- 所有行为变更先挂配置开关（§1.3），默认值按阶段验收节奏放开；
- 每阶段结束跑全量评测集出对比报告（P0–P1 人工跑批，P2-3 平台化后自动化）；
- P1 起 `execution_mode` 落 Run，新旧链路评测分组对比，FAST 与现链路语义等价验证后才切默认；
- 破坏性删除（§4 P2-6、LEGACY 清理）一律满足前置条件后独立 PR 执行。

---

## 1. 全局台账

### 1.1 新增顶层结构总览（P0–P3 全部新增包）

```
backend/
├─ apps/
│  ├─ evaluation/                          [新·P2] 评测与badcase平台（自 P0 下调，随运营闭环建设）
│  ├─ chatbi/
│  │  ├─ orchestration/pipeline/           [新·P1] 三模式编排（mode_router/fast/plan_mode/research/stages/events）
│  │  ├─ services/planning/                [扩·P1] + analysis_planner / plan_validation / plan_prompts / plan_patch / confidence
│  │  ├─ services/computation/             [新·P1] ComputeEngine（DuckDB）
│  │  ├─ services/generation/answer_composer/ [新·P1] 回答组装（claims/口径卡片/图表spec）
│  │  ├─ services/analysis/                [新·P2] 归因模板与研究报告
│  │  └─ orchestration/runner.py 等        [新·P2] 后台执行器与对账
│  ├─ semantic/services/compilation/       [新·P1] 编译器口径扩展模块组
│  ├─ semantic/services/authoring/         [新·P3] AI 辅助建模
│  ├─ semantic/services/interchange/       [新·P3] Ossie 导入导出
│  └─ retrieval/sources|query/             [扩·P2] knowledge/schema 投影与检索器、profile 覆盖
├─ common/
│  ├─ observability/                       [新·P1] OTEL metrics
│  └─ middleware/                          [新·P2] 限流
└─ scripts/eval/                           [新·P2] 评测 CLI（随评测平台）
```

### 1.2 alembic 迁移分配

| 编号 | 名称（拟） | 阶段 | 内容 |
|------|-----------|------|------|
| 110 | `verified_query_upgrade` | P0-5 | `data_training` 加列：status/source/verified_by/verified_at/semantic_plan(JSONB)/plan_fingerprint/use_as_onboarding |
| 111 | `evaluation_platform` | P2-3 | `eval_case` / `eval_run` / `eval_case_result` 三表（自 P0 下调） |
| 112 | `badcase_queue` | P2-3 | `chatbi_badcase` 表（自 P0 下调） |
| 113 | `agent_run_execution_mode` | P1-1 | `chatbi_agent_run` 加 `execution_mode` 列（fast/plan/research/react_legacy）+ 索引 |
| 114 | `dataset_instructions` | P1-10 | `headless_dataset_instruction` 表（module/content/version/enabled） |
| 115 | `result_set_registry` | P2-2 | `chatbi_result_set` 表（跨 Run 结果集引用；P1 期间结果集仅存 derived_state + Artifact） |
| 116 | `semantic_asset_grant` | P2-4 | `headless_asset_grant`（角色→数据集内指标/维度可见性） |
| 117 | `usage_daily_and_quota` | P2-4 | `chatbi_usage_daily`（租户/用户/阶段 token 与成本日聚合）+ 配额配置 |
| 118 | `data_access_audit` | P2-4 | 问数执行审计事实表（或 `sys_logs` 扩展，执行时二选一） |
| 119 | `knowledge_doc` | P2-5 | `knowledge_doc` / `knowledge_chunk` 表 |
| 120 | `retrieval_profile_override` | P2-5 | 检索门控阈值数据集级覆盖表 |
| 121 | `graph_sunset` | P2-6 | Graph 存量 Run 标记/清理（如需要） |
| 122 | `dataset_router`（可选） | P3-3 | 跨数据集会话解绑相关 |

### 1.3 配置开关新增/变更清单（`backend/common/core/config.py`）

| 阶段 | 键 | 默认 | 说明 |
|------|----|------|------|
| P0 | `AGENT_TRACING_ENABLED` | **False→True** | 观测默认开 |
| P0 | `AGENT_TRACING_SAMPLE_RATE` | **0.0→0.1** | 采样 10% 起步 |
| P0 | `CHATBI_TRIAGE_ENABLED` | True | 分诊 category 灰度开关 |
| P0 | `CHATBI_VALUE_BINDING_ENABLED` | True | VALUE 槽值归一 |
| P0 | `CHATBI_EXEMPLAR_CONTEXT_ENABLED` | True | verified/示例进标准路径 |
| P1 | `CHAT_AGENT_EXECUTION_MODES` | `"react_legacy"` | 启用的编排模式；灰度 FAST 使用 `"react_legacy,fast"`，PLAN/RESEARCH 按阶段放开 |
| P1 | `CHATBI_PLAN_MAX_QUERY_TASKS` | 5 | 计划节点上限 |
| P1 | `CHATBI_PLANNER_TIMEOUT_MS` | 60000 | 规划调用超时 |
| P1 | `CHATBI_COMPUTE_ENABLED` | True | ComputeEngine |
| P1 | `CHATBI_ANSWER_CITATION_ENFORCED` | True | 回答数字溯源强制 |
| P1 | `CHATBI_ASSISTED_FALLBACK_ENABLED` | False | ASSISTED 兜底全局闸（数据集级配置为主） |
| P1 | `RETRIEVAL_QUERY_TRACE_ENABLED` | True | 检索诊断持久化 |
| P1 | `CHATBI_MEMORY_RECALL_EXPERIMENT_ENABLED` / `TREATMENT_PERCENT` | True / 10 | 记忆灰度开启 |
| P1 | `OTEL_METRICS_ENABLED` | False | 指标出口（环境接好后开） |
| P2 | `CHAT_AGENT_BACKGROUND_RUNNER_ENABLED` / `CHAT_AGENT_RUNNER_WORKERS` | False / 4 | 后台执行器 |
| P2 | `CHATBI_RESEARCH_ENABLED` + `CHATBI_RESEARCH_MAX_QUERIES/MAX_LLM_CALLS/TIMEOUT_SECONDS` | False / 8 / 15 / 300 | RESEARCH 模式与预算 |
| P2 | `RATE_LIMIT_ENABLED` + `RATE_LIMIT_USER_QPS/TENANT_CONCURRENT_RUNS/DAILY_TOKEN_QUOTA` | False / … | 限流配额 |
| P2 | `AUDIT_DATA_ACCESS_ENABLED` | True | 问数执行审计 |
| P2 | `SSE_HEARTBEAT_SECONDS` | 15 | SSE 心跳 |

### 1.4 事件契约新增（`backend/apps/event/models/dto.py` 映射表扩展）

P1：`plan-created`、`plan-updated`（patch 时）、`task-started`、`task-finished`（载荷含 result_set 引用）、`compute-finished`；`answer` 载荷扩展口径卡片与 claims。P2：无新增事件（心跳为协议帧非事件）。前端投影同步见各阶段 `[前]` 项。

### 1.5 依赖新增

| 阶段 | 依赖 | 文件 |
|------|------|------|
| P1 | `duckdb`（进程内计算引擎） | `backend/pyproject.toml` + `backend/uv.lock` |
| P1 | `opentelemetry-sdk[metrics]`（已有 traces 依赖，补 metrics 出口） | 同上 |

### 1.6 里程碑与依赖关系

```
P0（第1-4周，全部并行）──► P1（第5-12周）──► P2（第13-24周）──► P3（持续）
关键依赖链：
  评测验收：P0–P1 用现有脚本人工跑批（golden_cases.jsonl）；评测平台（P2-3）就绪并标定后转自动化门禁
  P0-10 WIP合入 ──► P0-4 值归一（policy 稳定后）
  P1-1 计划模型 ──► P1-2 FAST ──► P1-3 PLAN ──► P1-4 Compute ──► P1-7 Composer
  P1-5 意图扩展 ∥ P1-6 编译器扩展（可并行先行，P1-3 依赖两者产物）
  P1-2/3 ──► P2-1 RESEARCH、P2-2 后台执行器
  P1 黄金集≥80% 且模式切换完成 ──► P2-6 Graph 下线评估
```

---

## 2. P0：止血与地基（第 1–4 周）

**目标回顾（doc 32 §7-P0，含 2026-08-16 调整）**：不动架构，消灭"必然失败"，建立度量基线。验收：黄金集严格正确 ≥65%（现有脚本人工跑批）；死局类失败 = 0。工作项相互独立可并行（P0-4 依赖 P0-10 先合入）；评测平台建设已下调至 P2-3，P0-8′ 仅保留脚本级最低保障。

### P0-1 澄清全覆盖（修 D2）

**做什么**：理解校验产出的全部 reason_code（约 20 个）都有澄清归宿。建"reason_code → 澄清卡片 builder"注册表；preflight 澄清从 3 类扩展到全类；无法构造选项的 reason 走拒答分支（带建议问法）。删除 `tool_visibility` 中"validation ≠ valid → 零工具"死局分支。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/services/understanding/clarification_catalog.py` | reason_code → 卡片 builder 注册表（选项来源：维度候选/指标候选/排名口径模板/意图选项） |
| 改 | `backend/apps/chatbi/services/understanding/validation.py` | 每个 reason_code 附澄清元数据（kind、候选来源、是否可澄清） |
| 改 | `backend/apps/chatbi/orchestration/agent/preparation.py` | preflight 分支（现约 839-937 行）改为遍历 catalog；澄清恢复按 kind 分发 |
| 改 | `backend/apps/chatbi/services/understanding/understanding_service.py` | `apply_question_understanding_clarification` 支持新 kind 回填 |
| 改 | `backend/apps/chatbi/orchestration/agent/tool_visibility.py` | 删除 `validation != valid → []` 分支（现约 53-56 行） |
| 改 | `backend/apps/chatbi/orchestration/agent/tools/interaction.py` | clarify 选项构建支持意图/指标/排名类卡片 |
| 改 | `backend/apps/chatbi/models/dto/question_understanding.py` | 澄清 DTO 加 `clarification_kind` 枚举 |
| 测 | `backend/tests/chatbi/test_clarification_catalog.py` [新] | 每个 reason_code 一个 case：澄清或拒答，绝不零工具 |

**[评]** 模糊问题 10 题（缺指标/意图冲突/排名歧义等）入黄金集，断言"澄清或拒答"。

### P0-2 分诊 category（修 D3）

**做什么**：统一理解输出 `category`（chitchat / data_query / meta_query / out_of_scope）；chitchat 直答退出、meta_query 用数据集语义资产目录作答、out_of_scope 拒答带理由；删除读取不存在字段的 `_allows_direct_answer` 死代码路径。

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/services/understanding/understanding_service.py`、`services/understanding/prompts.py` | 统一理解 prompt 增加 category 判定与输出 |
| 改 | `backend/apps/chatbi/models/dto/question_understanding.py` | 加 `category` 字段 |
| 改 | `backend/apps/chatbi/orchestration/agent/loop.py` | `_allows_direct_answer`（现约 675-683 行）改读新字段；三类非问数的直答/拒答收口 |
| 改 | `backend/apps/chatbi/orchestration/agent/lifecycle.py` | 直答收口路径（不产 SQL/图表产物，ChatRecord 正常 SUCCEEDED） |
| 新 | `backend/apps/chatbi/services/generation/capability_answer.py` | meta_query：基于 `DatasetSchema` 列指标/维度/示例问题生成"能查什么"回答 |
| 测 | `backend/tests/chatbi/test_triage_category.py` [新] | 四类 category 各自收口正确 |

**[评]** L0 会话题 5 题（闲聊/能力询问/越界）。

### P0-3 死角修补（修 D4/D7）

**做什么**：STRICT 计划非 PROVEN 时可见工具改为 `[clarify]`（语义歧义澄清）而非空集；`parse_time_range` 在"计划缺时间"时重新可见；finish 失败降级为"部分作答"（表格直出无图表），软收口复用同一模板；error_class 扩枚举使失败归因不再失真。

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/orchestration/agent/tool_visibility.py` | 非 PROVEN 分支（现约 66-71 行）与 `parse_time_range` 可见期（现约 76-83 行）修正 |
| 改 | `backend/apps/chatbi/orchestration/agent/loop.py` | 失败文案按真实原因；软收口走部分作答 |
| 改 | `backend/apps/chatbi/orchestration/agent/tools/core.py` | finish 失败（如图表字段越界）→ 降级部分作答而非 rejected 死循环 |
| 改 | `backend/apps/chatbi/services/generation/agent_finalization.py` | 增加 partial/degraded 模板（无图表、注明失败环节） |
| 改 | `backend/apps/chatbi/models/orm/agent_run.py` | `AgentErrorClass` 扩展：understanding_failed / binding_ambiguous / plan_invalid / finalize_failed（str 存储，无需迁移） |
| 测 | `backend/tests/chatbi/test_agent_dead_ends.py` [新] | D4/D7 场景回归：不再 budget_exhausted 死循环 |

### P0-4 VALUE 槽值归一（依赖 P0-10 先合入）

**做什么**：打通"维值检索→值归一"断链。理解产出的 `dimension_filter` 值生成 `value:N` 槽；命中 canonical_value 则替换（保留原词映射供口径卡片）、多命中进歧义澄清、未命中按数据集策略（保留原值+低置信标记 或 澄清）。

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/retrieval/projection/planner.py` | 生成 VALUE 槽（现仅 metric/dimension/term） |
| 改 | `backend/apps/retrieval/query/policy.py` | VALUE 决策：canonical 替换/歧义/未命中三分支（阈值沿用 profiles 已有 VALUE 档） |
| 改 | `backend/apps/retrieval/query/decision.py` | 澄清应用支持 VALUE 槽选择 |
| 改 | `backend/apps/retrieval/projection/payload.py` | `value_filters` 输出 canonical_value + `original_term` 映射 |
| 改 | `backend/apps/chatbi/orchestration/agent/preparation.py` | 值歧义澄清卡片（复用 dimension_filter_value 流） |
| 改 | `backend/common/core/config.py` | `CHATBI_VALUE_BINDING_ENABLED` |
| 测 | `backend/tests/retrieval/test_value_slot_planning.py` [新]；`test_semantic_binding_policy.py` [改] | 替换/歧义/未命中三分支 |

**[评]** 值归一 8-10 题（别名值/脏值/多义值）。

### P0-5 verified query 升级 + 进主路径

**做什么**：`data_training` 升级为带生命周期的 verified query 资产；语义绑定填充 `RetrievalBundle.exemplars`（相似 verified TopK）；语义包携带命中摘要进入 Agent/规划上下文，STRICT 路径同样消费。

| 操作 | 文件 | 说明 |
|------|------|------|
| 迁 | `backend/alembic/versions/110_verified_query_upgrade.py` [新] | 见 §1.2 |
| 改 | `backend/apps/knowledge/models/orm/sql_example.py`、`models/dto/sql_example.py` | 新列映射与 DTO |
| 改 | `backend/apps/knowledge/services/sql_example_service.py`、`repository/sqlmodel/sql_example_repository.py` | 状态生命周期（candidate/verified/deprecated） |
| 改 | `backend/apps/knowledge/api/sql_example.py` | verify/deprecate 状态流转端点 |
| 改 | `backend/apps/retrieval/sources/sql_example_projector.py`、`sql_example_indexing.py` | metadata 带 status/plan_fingerprint；verified 优先权重 |
| 改 | `backend/apps/retrieval/query/semantic_binding.py`（或 `query/service.py`，以实际装配点为准） | bundle.exemplars 填充 |
| 改 | `backend/apps/tool/tools/semantic.py` | 语义包附 exemplar 命中摘要（question+计划要点，非裸 SQL 全文） |
| 改 | `backend/apps/chatbi/orchestration/agent/prompts.py` | 标准路径消费说明（示例仅供参考、不得照抄表名） |
| 测 | `backend/tests/knowledge/test_verified_query_lifecycle.py`、`backend/tests/retrieval/test_exemplar_in_binding.py` [新] | |

### P0-6 指标级 filter_sql 进编译 + observation 结构化截断

**做什么**：单指标计划将 `headless_metric.filter_sql` 合并进 WHERE；多指标且 filter 不同的计划在校验层给出明确错误（完整 FILTER(WHERE) 方言支持放 P1-6）。observation 截断从"2000 字符腰斩成非法 JSON"改为结构化截断（保留 schema+统计+前 N 行 + truncated 标记）。

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/semantic/services/sql_compiler.py` | 单指标 WHERE 合并；来源标注进编译产物元数据 |
| 改 | `backend/apps/semantic/services/query/validation.py` | 多指标异构 filter → 明确校验错误 |
| 改 | `backend/apps/chatbi/orchestration/agent/tool_execution.py` | `_bounded_summary`（现约 1112-1119 行）结构化截断 |
| 测 | `backend/tests/semantic/test_sql_compiler_metric_filter.py` [新]；tests/chatbi 截断 case [改] | |

**[评]** 带口径过滤的指标 5 题。

### P0-7 检索 ACL 接通 + 空转 profile 移除

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/orchestration/agent/tools/base.py` | `RetrievalRequest` 传 principal/roles/permission_version（现约 101-113 行未传） |
| 改 | `backend/apps/retrieval/sources/semantic_indexing.py`、`sql_example_indexing.py` | `acl_policy` 真实写入（P0=租户+visibility；角色化放 P2-4） |
| 改 | `backend/apps/retrieval/query/profiles.py` | 移除 KNOWLEDGE_EVIDENCE / SCHEMA_FALLBACK 空转注册（P2-5 实现后再注册） |
| 测 | `backend/tests/retrieval/test_acl_scope.py` [新] | 越权资产不可见 |

### P0-8′ 评测最低保障（脚本级，≤1 人天）【评测平台建设已下调至 P2-3】

**做什么**：按 2026-08-16 决策，不在 P0 建评测平台（无新 app、无新表、无 CI 门禁）。仅保留最低保障：黄金题集以版本化 JSONL 文件维护（P0 各项 `[评]` 题落此文件），阶段验收与重大合并后用现有脚本人工跑批出报告（沿用 fresh_20 报告样式，人工核验判分）。评测平台全套（eval 三表、分层 runner、结果集判分、漂移检测、CI 门禁、badcase 队列）移至 **P2-3** 与运营闭环合并建设。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/scripts/golden_cases.jsonl` | 黄金题集（question/dataset/期望要点/tags），随 PR 评审演进 |
| 改 | `backend/scripts/run_mall_store_agent_fresh_20.py` | 支持从外部题集文件读题 + 汇总报告输出（改动最小化） |
| — | 旧 `evaluate_*` 脚本 | 保持现状，人工触发 |

**取舍说明**：P0/P1 的质量数字依赖人工跑批与人工判分（与现状一致）；判分规则标定、自动回归、趋势追踪推迟到 P2-3。换来 P0 释放约 1-1.5 人周投入到能力项。

### P0-9 观测默认开（badcase 队列与反馈闭环随评测平台移至 P2-3）

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/common/core/config.py` | TRACING 默认开、采样 0.1（§1.3） |
| 可选 | `backend/apps/conversation/`（chat_record 加 feedback JSONB 字段，约半天） | 若希望赞踩数据从 P0 就开始积累可做；处理闭环（badcase 队列/运营台）仍在 P2-3。不做则线上反馈自 P2 起才有积累 |

### P0-10 WIP 合入与守护

**做什么**：合入当前未提交改动（understanding 统一单遍理解、retrieval decision/policy 收敛、多轮维度继承），补齐固化测试；在 docs/tech/23、25 增补"与代码不一致"勘误注记（doc 32 §3 引用的差异清单）。不新增文件，涉及文件即当前 git status 中的改动集。

### P0 文件树图（全部工作项汇总）

```
backend/
├─ alembic/versions/
│  └─ 110_verified_query_upgrade.py                      [新] P0-5
├─ apps/
│  ├─ chatbi/
│  │  ├─ models/dto/question_understanding.py            [改] P0-1/2 category+clarification_kind
│  │  ├─ models/orm/agent_run.py                         [改] P0-3 error_class 扩展
│  │  ├─ orchestration/agent/
│  │  │  ├─ tool_visibility.py                           [改] P0-1/3 删死局分支
│  │  │  ├─ loop.py                                      [改] P0-2/3 分诊收口/部分作答
│  │  │  ├─ lifecycle.py                                 [改] P0-2 直答收口
│  │  │  ├─ preparation.py                               [改] P0-1/4 澄清catalog/值歧义
│  │  │  ├─ tool_execution.py                            [改] P0-6 结构化截断
│  │  │  ├─ prompts.py                                   [改] P0-5 exemplar 消费说明
│  │  │  └─ tools/
│  │  │     ├─ base.py                                   [改] P0-7 ACL 透传
│  │  │     ├─ core.py                                   [改] P0-3 finish 降级
│  │  │     └─ interaction.py                            [改] P0-1 新卡片类型
│  │  └─ services/
│  │     ├─ understanding/
│  │     │  ├─ clarification_catalog.py                  [新] P0-1
│  │     │  ├─ validation.py                             [改] P0-1 澄清元数据
│  │     │  ├─ understanding_service.py                  [改] P0-1/2/10
│  │     │  └─ prompts.py                                [改] P0-2 category
│  │     └─ generation/
│  │        ├─ capability_answer.py                      [新] P0-2 meta 问答
│  │        └─ agent_finalization.py                     [改] P0-3 partial 模板
│  ├─ semantic/services/
│  │  ├─ sql_compiler.py                                 [改] P0-6 指标 filter
│  │  └─ query/validation.py                             [改] P0-6
│  ├─ retrieval/
│  │  ├─ projection/planner.py                           [改] P0-4 VALUE 槽
│  │  ├─ projection/payload.py                           [改] P0-4 canonical 输出
│  │  ├─ query/policy.py                                 [改] P0-4/10
│  │  ├─ query/decision.py                               [改] P0-4/10
│  │  ├─ query/semantic_binding.py                       [改] P0-5 exemplars 填充
│  │  ├─ query/profiles.py                               [改] P0-7 移除空转注册
│  │  └─ sources/{semantic_indexing,sql_example_indexing,sql_example_projector}.py  [改] P0-5/7
│  ├─ knowledge/
│  │  ├─ models/orm/sql_example.py                       [改] P0-5 生命周期列
│  │  ├─ models/dto/sql_example.py                       [改] P0-5
│  │  ├─ services/sql_example_service.py                 [改] P0-5
│  │  ├─ repository/sqlmodel/sql_example_repository.py   [改] P0-5
│  │  └─ api/sql_example.py                              [改] P0-5 状态流转
│  └─ tool/tools/semantic.py                             [改] P0-5 语义包附 exemplar
├─ common/core/config.py                                 [改] P0 开关（§1.3）
├─ scripts/golden_cases.jsonl                            [新] P0-8′ 黄金题集（各项 [评] 题落点）
├─ scripts/run_mall_store_agent_fresh_20.py              [改] P0-8′ 支持外部题集与汇总报告
└─ tests/
   ├─ chatbi/{test_clarification_catalog,test_triage_category,test_agent_dead_ends}.py  [新]
   ├─ retrieval/{test_value_slot_planning,test_exemplar_in_binding,test_acl_scope}.py   [新]
   ├─ knowledge/test_verified_query_lifecycle.py         [新]
   └─ semantic/test_sql_compiler_metric_filter.py        [新]
```

### P0 验收清单

黄金集（扩充后 ~50 题，`golden_cases.jsonl`）严格正确 ≥65%（阶段末脚本人工跑批出报告，fresh_20 样式）；D2/D3/D4/D7 场景回归全绿（死局 = 0）；值归一/exemplar 开关开启后正确率不回退；trace 采样数据可查。

**2026-08-16 真实环境验收结果**：达标。详细报告见 `docs/test-results/p0-golden-2026-08-16/`。自动字符串判分为 14/28，其中 G004/G005 为日期展示格式差异、G006 为小数展示精度差异、G008 为答案省略中间日期，人工核对数值后均正确。结合 22 条澄清/拒答/元问题行为题，人工初审为 40/50（80%）。未通过的 10 条分为：业务结果错误 G010/G013/G014/G016/G046/G047；多余澄清 G009/G017/G036/G044。这些运行都有可审计终态，不属于 D2/D3/D4/D7 死局。

---

## 3. P1：复杂查询能力（第 5–12 周）

**目标回顾（doc 32 §7-P1）**：AnalysisPlan/ResultStore/ComputeEngine 落地，FAST/PLAN 双模式收敛，指标表达力补课，AnswerComposer 与置信路由。验收：对比/复合口径新题 ≥80%；FAST ≤4 次 LLM；数字溯源 100%；黄金集 ≥80%。

### P1-1 计划与结果模型（地基，先行）

**实施状态（2026-08-16）：已完成。** `AnalysisPlan` 提供 query/compute 判别联合及 JSON Schema；`ResultStore` 通过现有 Artifact 网关完成命名结果集注册、归属读取和有界摘要；`chatbi_agent_run.execution_mode` 默认 `react_legacy`，计划与结果集快照统一由 Repository 校验。旧 `query-0`、`last_execution` 和 `full_data` 仅作为当前 Agent 投影阶段的必要双写，不承担新模型的主数据职责；P1-2 完成模式切换后再评估删除这些过渡字段。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/models/dto/analysis_plan.py` | AnalysisPlan / QueryTask / ComputeTask / PlanEdge / PresentationHint / ResultSetRef（含 JSON Schema 供规划 LLM 结构化输出） |
| 新 | `backend/apps/chatbi/services/execution/result_store.py` | 命名结果集注册/读取/摘要生成；底层复用 `result_artifacts.py` 网关，artifact 命名 `result:{plan_id}:{node_id}` 替代 `query-0` |
| 迁 | `backend/alembic/versions/113_agent_run_execution_mode.py` [新] | `execution_mode` 列 |
| 改 | `backend/apps/chatbi/models/orm/agent_run.py` | 列映射；derived_state 内嵌 `analysis_plan` / `result_sets` 结构注释 |
| 改 | `backend/apps/chatbi/repository/sqlmodel/agent_run_repository.py` | 快照读写含计划与结果集注册表 |
| 改 | `backend/apps/chatbi/services/execution/result_artifacts.py` | 多结果集命名；保留旧命名读兼容 |
| 改 | `backend/apps/chatbi/orchestration/agent/tool_results.py` | execute_sql 结果写入 ResultStore（旧单槽 last_execution 过渡期双写） |
| 测 | `backend/tests/chatbi/{test_analysis_plan_model,test_result_store}.py` [新] | |

### P1-2 三模式编排骨架 + FAST 通道

**实施状态（2026-08-16）：P1-2 已完成骨架与 FAST 接入，P1-3 已完成 PLAN 规则规划通道。** 新增 `pipeline/` 下的模式路由、阶段封装、FAST/PLAN 管道、RESEARCH 占位和计划/任务/计算事件；Agent 在问题理解完成后按开关和查询形态选择模式。FAST 复用现有语义检索、编译、校验、执行和回答服务，并通过 P1-1 的 `AnalysisPlan`/`ResultStore` 写入单节点计划与命名结果集，不进入 `AgentReasoner`/`AgentToolExecutor` 的 ReAct 循环。PLAN 已支持单查询与 CROSS_MODEL `multi_query_plans` 转换为多 QueryTask、DAG/节点上限校验、逐节点编译校验执行和计划快照持久化；规划 prompt 与 JSON Schema 已提供，后续可接入独立规划模型。为保证迁移期行为，默认开关仍为 `react_legacy`；FAST/PLAN 与旧链路等价性和真实灰度跑批作为阶段验收项。

**做什么**：新建 `orchestration/pipeline/` 包承载确定性编排；`mode_router` 按意图/查询形态/数据集配置确定模式；FAST = 理解→绑定→规则直出单节点计划→编译→执行→作答，**不经工具层与 ReAct 循环**（直接调用语义工具背后的服务：检索、编译、DatasourceQueryService）。`orchestration/agent/`（ReAct）保留为迁移期后备（`react_legacy` 模式）与 P2 RESEARCH 的宿主基建。

```
backend/apps/chatbi/orchestration/pipeline/       [新]
├─ __init__.py
├─ mode_router.py        确定性模式选择（意图×形态×数据集配置×开关）
├─ stages.py             阶段服务封装：bind / plan / validate / execute / compute / answer
├─ fast.py               FAST 管道
├─ plan_mode.py          PLAN 管道（P1-3）
├─ research.py           RESEARCH（P2-1 占位）
└─ events.py             plan/task/compute 事件发布（复用 EventPublisher）
```

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/orchestration/agent/service.py` | 入口按 mode_router 分发；`react_legacy` 保留原路径 |
| 改 | `backend/apps/chatbi/orchestration/agent/preparation.py` | 理解/绑定/记忆装载抽取为可复用函数（pipeline 与 legacy 共用） |
| 改 | `backend/apps/chatbi/orchestration/agent/composition.py` | 组装 pipeline 依赖（复用现有服务装配） |
| 改 | `backend/apps/event/models/dto.py` | 新事件名映射（§1.4） |
| 改 | `backend/common/core/config.py` | `CHAT_AGENT_EXECUTION_MODES` |
| 前 | `frontend/src/views/chat/execution-component/`（`agentTimelineProjection.ts` 等） | Timeline 投影支持 plan/task 事件 |
| 测 | `backend/tests/chatbi/{test_mode_router,test_fast_pipeline}.py` [新] | FAST 与 react_legacy 同题等价对比测试 |

### P1-3 PLAN 规划通道

**实施状态（2026-08-16）：已完成规则规划、计划校验、确定性 QueryTask 管道和 ComputeTask 接入。** `AnalysisPlanner` 可将单查询绑定结果和 CROSS_MODEL 的 `multi_query_plans` 转换为 `AnalysisPlan`；严格模式现在为每个子计划保存独立 `SemanticQueryPlan` 和验证报告，时间回投影会同步刷新全部子计划，`PlanPipeline` 在执行前逐个检查 `PROVEN`，并按 QueryTask 选择对应计划，复用严格编译、权限和指纹校验。专项测试已覆盖子计划顺序、独立指纹、非 PROVEN 拒绝和时间绑定。规划 prompt 与 JSON Schema 已提供，后续可接入独立规划模型。仍需真实 CROSS_MODEL 数据集完成端到端评测，复杂偏移的双 QueryTask 也需继续验证；不再使用原先的 `PLAN_STRICT_MULTI_QUERY_NOT_READY` 直接拒绝路径。

**真实环境检查（2026-08-17）**：数据集 243 已由人工确认 5 个模型、41 个指标、47 个物理维度及同模型能力，发布为 `READY`、契约版本 2，并切换 `semanticEnforcement: STRICT`；readiness 的模型粒度、指标契约、维度绑定、能力、关系和默认时间维度覆盖率均为 1.0。四类真实严格子计划均达到 `PROVEN`，生成 SQL 已在数据源 13 的 MySQL 执行成功。真实 Agent 检查补齐了三项边界问题：问题理解把可选对象输出为 `null` 时，检索请求入口统一归一为空集合；检索槽位的 `asset_id` 正确绑定为计划的 `physical_dimension_id`；严格编译按资产集合校验而非错误比较 Schema 顺序，并将仅用于时间过滤的时间维度纳入 `used_assets`。这些修复均有回归测试。临时服务验证时关闭了缺失 OpenTelemetry 依赖的 tracing；该环境依赖问题不改变 STRICT 契约门槛。仍需让问题理解明确输出 `multi_query/cross_model`，再完成 Agent PLAN 的端到端 CROSS_MODEL 评测。
**真实 PLAN 验证补充（2026-08-17）**：在临时服务开启 `CHAT_AGENT_EXECUTION_MODES=plan`、关闭缺失依赖的 tracing 后，使用两个独立指标问题验证了 PLAN 路径：运行模式为 `plan`，生成 `q1/q2` 两个 QueryTask，两个任务均真实执行成功，最终计划状态为 `PROVEN`。为避免问题理解未显式标记 `multi_query` 时仍落到旧 ReAct 链路，模式选择在多指标理解结果下补充进入 PLAN 的规则。无比较关系的多 QueryTask 当前以首个结果作为主展示；需要同时汇总或计算多个结果时必须由问题理解输出比较/计算形态，生成 ComputeTask。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/services/planning/analysis_planner.py` | 规则直出（单查询/CROSS_MODEL 的 `multi_query_plans` 转多 QueryTask）+ LLM 规划入口 |
| 新 | `backend/apps/chatbi/services/planning/plan_prompts.py` | 规划 prompt（输入：理解 JSON+绑定资产+verified 命中+instructions+ComputeTask 操作说明）与 JSON Schema |
| 新 | `backend/apps/chatbi/services/planning/plan_validation.py` | DAG 无环/节点上限/节点引用/ComputeTask 输入校验，并在执行前检查 QueryTask 是否已 PROVEN |
| 新 | `backend/apps/chatbi/orchestration/pipeline/plan_mode.py` | 规则规划→统一校验→逐 QueryTask 编译/SQL 校验/执行→计划与结果集快照；ComputeTask 按 DAG 执行，STRICT 多查询逐子计划检查 PROVEN 后执行 |
| 改 | `backend/apps/chatbi/services/understanding/model_invocation.py` | 规划阶段复用的结构化模型调用边界待接入；当前先提供 prompt、JSON Schema 和模型计划反序列化入口 |
| 改 | `backend/apps/retrieval/projection/payload.py` | `multi_query_plans` 输出结构与规划直出接口对齐 |
| 测 | `backend/tests/chatbi/{test_analysis_planner,test_plan_validation}.py` [新] | 含"规划幻觉资产被校验拦截"case |

**[评]** 多查询计划题 10+（跨模型拆分/多时段/多指标并列）。

### P1-4 ComputeEngine（DuckDB）

**实施状态（2026-08-16）：已完成。** 新增进程内 DuckDB `ComputeEngine`、操作白名单 SQL 编译和表达式校验；支持 `compare`、`growth`、`share`、`topn_other`、`pivot`、`expr`，输入从 ResultStore 读取，输出作为 `ResultSetKind.COMPUTE` 命名结果集写回，并发布 `compute-finished` 事件。除零、空结果集、连接键不齐、未知字段、子查询和外部表引用均有明确边界处理。`CHATBI_COMPUTE_ENABLED` 默认开启；STRICT 多查询的多计划执行接线已完成，复杂时间偏移和跨模型真实数据集评测仍需继续验证。

```
backend/apps/chatbi/services/computation/         [新]
├─ __init__.py / errors.py
├─ engine.py             DuckDB 会话管理、结果集注册为表、执行与产出
├─ ops.py                compare / growth / share / topn_other / pivot / expr → DuckDB SQL 编译
└─ expr_validator.py     受限表达式 AST 白名单校验（四则/聚合/CASE，禁子查询与外部引用）
```

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/pyproject.toml` + `backend/uv.lock` | 加 `duckdb` |
| 改 | `backend/apps/chatbi/orchestration/pipeline/plan_mode.py` | compute 阶段接入 PLAN 管道；通用 `stages.py` 保留可注入 compute 接口 |
| 改 | `backend/common/core/config.py` | `CHATBI_COMPUTE_ENABLED` |
| 测 | `backend/tests/chatbi/computation/{test_ops,test_expr_validator,test_engine}.py` [新] | 每算子边界 case（空集/键不齐/除零） |

### P1-5 意图与时间扩展

**实施状态（2026-08-16）：已完成。** 保留旧 `time_range` 单区间投影，同时新增 `time_ranges[]` 和 `ComparisonSpec`；意图类型增加 `composition`、`multi_step`。TemporalPlan/ResolvedTemporalPlan 支持比较元数据和多区间，`derive_comparison_ranges` 对同比、环比按自然月确定性推导，对自定义基期要求显式区间；旧 `project_time_range_payload()` 在单区间场景保持原行为。问题理解、Graph 投影、校验与澄清目录已同步，`parse_time_range` 支持批量输入并返回 ranges。新增比较时间单测；现有 temporal/chatbi/agent/retrieval/event 测试通过。

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/models/dto/question_understanding.py` | intent_type + comparison/composition/multi_step；`time_ranges[]`；`ComparisonSpec{base, compare[], method}` |
| 改 | `backend/apps/chatbi/services/understanding/{understanding_service,prompts,validation}.py` | 提示词、归一化、新形态校验与澄清（catalog 注册新 reason） |
| 改 | `backend/apps/temporal/{plan,resolver,binder}.py` | 多区间解析与对比期推导（`derive_comparison_ranges`：同比/环比/自定义基期） |
| 改 | `backend/apps/chatbi/orchestration/agent/tools/temporal.py` | `parse_time_range` 支持多区间输出 |
| 改 | `backend/apps/retrieval/projection/planner.py` | 多时段下时间维槽复用 |
| 测 | `backend/tests/temporal/test_comparison_ranges.py` [新]；tests/chatbi 理解扩展 case | |

**[评]** 同环比/双时段/占比 12+ 题。

### P1-6 指标表达力（编译器扩展）

**实施状态（2026-08-16）：首版完成。** 新增 `apps/semantic/services/compilation` 模块组：`metric_expansion` 展开 `metric_refs` 并对简单 ratio 自动加入 `NULLIF`；`time_offset` 对固定月/季/年粒度渲染 `LAG`，复杂或自定义区间明确返回 `dual_query` 决策；`preaggregation` 和 `snapshot` 提供受控子查询及期初/期末窗口表达式。语义查询计划新增 `having/time_offset/subplans`，规划阶段保留派生指标引用，验证阶段增加比率保护、时间偏移和预聚合粒度校验；SQL 编译器接入派生指标、HAVING、快照、固定周期时间偏移和关系契约安全检查。复杂偏移不静默生成错误单 SQL，由上层按现有 QueryTask + ComputeTask 路径处理。

```
backend/apps/semantic/services/compilation/       [新]
├─ __init__.py
├─ metric_expansion.py   metric_refs 展开：derived（引用组合）/ ratio（分子分母子查询对齐后相除）
├─ time_offset.py        同环比时间偏移的单 SQL 渲染（不可行时声明降级为双 QueryTask）
├─ preaggregation.py     PRE_AGGREGATE_REQUIRED → 预聚合子查询渲染
└─ snapshot.py           snapshot_aggregation → 期末/期初取值渲染
```

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/semantic/services/sql_compiler.py` | 编排 compilation 模块；HAVING 支持；join 路径改用关系契约（cardinality/metric_propagation）选择 |
| 改 | `backend/apps/semantic/services/query/planning.py` | 计划支持 metric_refs 展开、时间偏移、预聚合标记落地 |
| 改 | `backend/apps/semantic/services/query/validation.py` | 新口径校验（ratio 分母保护/偏移合法性/预聚合粒度） |
| 改 | `backend/apps/semantic/models/dto/semantic_query.py` | 计划 DTO 扩展（having/time_offset/子计划） |
| 测 | `backend/tests/semantic/compilation/` [新目录] | 每口径一个测试文件 + golden SQL fixtures（`backend/tests/semantic/fixtures/`） |

**[评]** 派生/比率/预聚合/快照口径各 3-5 题。

### P1-7 AnswerComposer（替代 finish 双 LLM）

**实施状态（2026-08-16）：首版完成。** 新增 `answer_composer/`：模型只生成结构化回答与 claims，服务端使用真实结果集字段/行校验数字；绑定失败最多受控重试一次，仍失败时返回真实结果表。口径卡片由理解/语义/执行元数据确定性生成，图表 spec 按结果形状规则推导（table、bar、line、area、pie、combo、pivot、KPI）。FAST/PLAN 和显式注入的 finish 可使用 Composer；`react_legacy` 默认继续使用原 `AgentFinalizationService`，避免迁移期行为变化。answer 与 run-finished 事件携带 claims、口径卡片和 chart spec。专项测试覆盖数字编造拦截、单次重试、表格降级和摘要输入不发送全量行。

```
backend/apps/chatbi/services/generation/answer_composer/   [新]
├─ __init__.py
├─ composer.py           分级作答模板：fast / plan / partial（软收口共用）
├─ claims.py             结构化 claim 输出 + 数字溯源校验（不通过→降级表格直出+重试1次）
├─ caliber_card.py       口径卡片（指标定义/维度/过滤含值映射/时间/SQL/是否认证口径）
├─ chart_spec.py         图表 spec 规则推导（table/bar 分组堆叠/line 多系列/area/pie/combo/pivot/KPI）+ LLM 微调选择
└─ prompts.py
```

| 操作 | 文件 | 说明 |
|------|------|------|
| 改 | `backend/apps/chatbi/orchestration/agent/tools/core.py` | finish 支持显式注入 Composer；默认 `react_legacy` 保留原收口 |
| 改 | `backend/apps/chatbi/orchestration/pipeline/stages.py` | answer 阶段 |
| 改 | `backend/apps/chatbi/models/dto/{final_reply,answer_generation}.py` | 输出 DTO 扩展（claims/caliber/chart_spec） |
| 改 | `backend/apps/event/models/dto.py` | answer 载荷扩展 |
| 改 | `backend/apps/chatbi/services/generation/agent_finalization.py` | P1 期间保留为 legacy 过渡适配，待新模式稳定后再评估删除 |
| 前 | `g2-ssr/`（新图表模板）+ `frontend/src/views/chat/answer/`、`chat-block/` | 图表类型与口径卡片展示 |
| 测 | `backend/tests/chatbi/generation/{test_answer_composer,test_claims_binding}.py` [新] | 溯源校验含"编造数字被拦截"case |

### P1-8 置信度路由 + ASSISTED 兜底

**实施状态（2026-08-16）：首版完成。** 新增 `planning/confidence.py`，以绑定证据、计划校验、verified 命中和通道生成 `direct/disclose/clarify/reject` 四档结果及可展示依据；FAST 记录该结果并在 ASSISTED 数据集上按开关进入兜底。新增 `generation/fallback_sql.py`，使用物理 Schema 与 exemplar 生成只读 SQL，随后复用 `DatasourceQueryService.validate/execute`，结果统一标记 `sql_source=assisted_fallback`、`certified=false`。数据集 `query_config.semanticEnforcement` 现在只接受 STRICT/ASSISTED/LEGACY，默认开关 `CHATBI_ASSISTED_FALLBACK_ENABLED=false`。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/services/planning/confidence.py` | 分档函数（绑定证据×校验状态×verified 命中×通道）+ 依据结构（供口径卡片展示） |
| 新 | `backend/apps/chatbi/services/generation/fallback_sql.py` | 受控 NL2SQL：物理 schema+exemplar 上下文生成，复用 `DatasourceQueryService` 三道闸校验，产物标注非认证口径 |
| 改 | `backend/apps/tool/tools/semantic.py` | `semanticEnforcement` 取值扩展 STRICT/ASSISTED/LEGACY 的解析（`_resolve_enforcement`） |
| 改 | `backend/apps/semantic/models/dto/dataset.py` + `api/datasets.py` | query_config 校验与文档化 |
| 改 | `backend/apps/chatbi/orchestration/pipeline/{mode_router,stages}.py` | 四档路由接线（直答/披露/澄清/拒答或兜底） |
| 改 | `backend/common/core/config.py` | `CHATBI_ASSISTED_FALLBACK_ENABLED` |
| 测 | `backend/tests/chatbi/{test_confidence_routing,test_assisted_fallback}.py` [新] | |

### P1-9 多轮 patch / 记忆灰度 / 观测 metrics / 检索 trace

**实施状态（2026-08-16）：首版完成。** 新增 `plan_patch.py`，仅允许显式替换时间范围、维度和筛选，补丁应用后清除旧编译结果并重新执行计划校验；重写输出新增 `plan_patch`，无法安全应用时回退完整理解。记忆召回默认开启 10% treatment 分桶。新增 `common/observability/metrics.py`，统一记录阶段耗时、Run、token、澄清/拒答/兜底结果，OTEL 依赖按开关惰性加载，缺失时抛出明确 `ImportError`。已有 `retrieval_query_trace` 表接入语义绑定成功路径，保存 query hash、权限范围、通道诊断、候选排名、决策、策略版本和 generation；纯内存测试适配器不伪造持久化记录。

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/services/planning/plan_patch.py` | 追问增量修改（换时间/维度/筛选 patch 上一计划，重校验后执行） |
| 改 | `backend/apps/chatbi/services/understanding/understanding_service.py` | 重写阶段输出 followup patch 意图（message_type 扩展） |
| 改 | `backend/apps/chatbi/orchestration/agent/preparation.py` | patch 路径接入（命中则跳过全量理解） |
| 新 | `backend/common/observability/__init__.py`、`metrics.py` | OTEL metrics：run 计数/各阶段延迟直方图/token 计数/澄清率/拒答率/兜底率 |
| 改 | `backend/apps/chatbi/orchestration/pipeline/stages.py`、`backend/apps/datasource/services/query_service.py` | 埋点 |
| 改 | `backend/apps/retrieval/query/service.py` | `retrieval_query_trace` 写入（开关控制） |
| 改 | `backend/common/core/config.py` | 记忆灰度开启（TREATMENT=10）、`RETRIEVAL_QUERY_TRACE_ENABLED`、`OTEL_METRICS_ENABLED` |
| 测 | `backend/tests/chatbi/test_plan_patch.py`、`backend/tests/retrieval/test_query_trace_persist.py` [新] | |

### P1-10 instructions 资产 + STRICT 推广

**实施状态（2026-08-16）：首版完成。** 新增迁移 114、数据集级 instructions ORM/DTO/CRUD API，Schema 仅投影启用指令并按模块和版本排序；`question_categorization` 已注入统一问题理解固定槽位，`sql_generation` 已接入规划提示词构造函数。backfill 脚本新增逐数据集 STRICT readiness 报告，输出契约覆盖率、当前 enforcement、指令模块配置、未达标原因和是否可推广；STRICT 硬门槛仍只取契约覆盖率，指令缺失单独作为治理项报告，脚本不自动修改 `semanticEnforcement`。已补充 service、Schema 投影、提示词注入和 readiness 测试。

| 操作 | 文件 | 说明 |
|------|------|------|
| 迁 | `backend/alembic/versions/114_dataset_instructions.py` [新] | `headless_dataset_instruction` |
| 新 | `backend/apps/semantic/models/orm/instruction.py`、`models/dto/instruction.py` | module ∈ {sql_generation, question_categorization} |
| 新 | `backend/apps/semantic/repository/sqlmodel/instruction_repository.py`、`services/instruction_service.py`、`api/instructions.py` | CRUD 与版本 |
| 改 | `backend/apps/semantic/api/router.py` | 挂载 |
| 改 | `backend/apps/semantic/repository/sqlmodel/schema_loader.py` + `services/builders/schema_builder.py` | DatasetSchema 附 instructions |
| 改 | `backend/apps/chatbi/services/understanding/prompts.py`、`services/planning/plan_prompts.py` | 固定槽位注入两模块指令 |
| 改 | `backend/scripts/backfill_semantic_contract.py` | 批量执行与覆盖率报告输出（STRICT 推广工具） |
| 前 | 语义管理页 instructions 编辑 | |
| 测 | `backend/tests/semantic/test_instruction_service.py` [新] | |

**STRICT 推广节奏**（贯穿 P1）：每数据集 = 跑 backfill → 契约覆盖率达标 → `semanticEnforcement: STRICT` → 评测回归 → 下一个。

### P1 文件树图

```
backend/
├─ alembic/versions/{113_agent_run_execution_mode,114_dataset_instructions}.py     [新]
├─ pyproject.toml / uv.lock                                  [改] +duckdb +otel-metrics
├─ apps/
│  ├─ chatbi/
│  │  ├─ models/dto/analysis_plan.py                         [新] P1-1
│  │  ├─ models/dto/{question_understanding,final_reply,answer_generation}.py  [改] P1-5/7
│  │  ├─ models/orm/agent_run.py                             [改] P1-1 execution_mode
│  │  ├─ orchestration/
│  │  │  ├─ pipeline/                                        [新] P1-2/3（mode_router/stages/fast/plan_mode/events + research 占位）
│  │  │  └─ agent/{service,preparation,composition,tool_results,tools/core,tools/temporal}.py  [改]
│  │  ├─ repository/sqlmodel/agent_run_repository.py         [改] P1-1
│  │  └─ services/
│  │     ├─ planning/{analysis_planner,plan_prompts,plan_validation,plan_patch,confidence}.py  [新]
│  │     ├─ computation/                                     [新] P1-4（engine/ops/expr_validator）
│  │     ├─ execution/{result_store.py [新], result_artifacts.py [改]}
│  │     ├─ generation/answer_composer/                      [新] P1-7（composer/claims/caliber_card/chart_spec/prompts）
│  │     ├─ generation/{fallback_sql.py [新] P1-8, agent_finalization.py [删·P1末]}
│  │     └─ understanding/{understanding_service,prompts,validation,model_invocation}.py  [改] P1-5/9
│  ├─ semantic/
│  │  ├─ services/compilation/                               [新] P1-6（metric_expansion/time_offset/preaggregation/snapshot）
│  │  ├─ services/{sql_compiler.py,query/planning.py,query/validation.py,instruction_service.py [新]}  [改/新]
│  │  ├─ models/{orm/instruction.py [新], dto/{instruction.py [新], semantic_query.py [改], dataset.py [改]}}
│  │  ├─ repository/sqlmodel/{instruction_repository.py [新], schema_loader.py [改]}
│  │  └─ api/{instructions.py [新], router.py [改], datasets.py [改]}
│  ├─ retrieval/{projection/{planner,payload}.py, query/service.py}              [改] P1-3/5/9
│  ├─ temporal/{plan,resolver,binder}.py                     [改] P1-5
│  ├─ tool/tools/semantic.py                                 [改] P1-8 enforcement 三态
│  ├─ event/models/dto.py                                    [改] P1-2/7 新事件与载荷
│  └─ datasource/services/query_service.py                   [改] P1-9 埋点
├─ common/{observability/ [新] P1-9, core/config.py [改]}
├─ scripts/backfill_semantic_contract.py                     [改] P1-10
└─ tests/
   ├─ chatbi/{test_analysis_plan_model,test_result_store,test_mode_router,test_fast_pipeline,
   │          test_analysis_planner,test_plan_validation,test_plan_patch,
   │          test_confidence_routing,test_assisted_fallback}.py                 [新]
   ├─ chatbi/computation/ + chatbi/generation/               [新]
   ├─ semantic/compilation/ + test_instruction_service.py    [新]
   ├─ temporal/test_comparison_ranges.py                     [新]
   └─ retrieval/test_query_trace_persist.py                  [新]
frontend/src/views/chat/{execution-component,answer,chat-block}/ + g2-ssr/       [前] P1-2/7
```

### P1 验收清单

对比/复合口径/多查询新题 ≥80%；FAST 路径 LLM ≤4 次且与 react_legacy 同题等价（脚本双跑对比）；回答数字溯源校验 100% 通过（或降级表格）；黄金集 ≥80%（仍为脚本人工跑批）；`CHAT_AGENT_EXECUTION_MODES` 默认切到 `fast,plan`（react_legacy 保留开关一个迭代后再摘）。

---

## 4. P2：企业化运营（第 13–24 周）

**目标回顾（doc 32 §7-P2，含 2026-08-16 调整）**：RESEARCH 模式、后台执行器、**评测平台（自 P0 下调）**与运营闭环产品化、权限/审计/限流/成本、检索平台补全、Graph 下线。验收：黄金集 ≥85%（评测平台标定后的自动判分口径）且发布判定通过；运营看板上线；断连/重启无悬挂 Run。

### P2-1 RESEARCH 模式（归因）

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/orchestration/pipeline/research.py` | 有界研究循环宿主（预算：查询数/LLM 数/墙钟；复用 agent loop 的取消/预算基建） |
| 新 | `backend/apps/chatbi/services/analysis/__init__.py`、`attribution.py` | 归因模板：加法指标差值分解、比率指标简化贡献、TopN 异动定位 |
| 新 | `backend/apps/chatbi/services/analysis/report_composer.py` | 研究报告模板（发现/证据引用/建议/未验证假设），复用 answer_composer 的 claims 机制 |
| 新 | `backend/apps/chatbi/orchestration/agent/tools/research.py` | 研究循环内工具：提出子问题→生成 QueryTask（走绑定/校验/编译全链）→读结果摘要 |
| 改 | `backend/apps/chatbi/orchestration/pipeline/mode_router.py` | "为什么"类触发 + 数据集开关 + 用户显式升级 |
| 改 | `backend/apps/chatbi/orchestration/agent/budget.py`、`backend/common/core/config.py` | 模式化预算组 |
| 测 | `backend/tests/chatbi/test_attribution_template.py` [新] + research e2e 评测题 | |

**[评]** 归因/研究题 8-10 题（LLM-judge 观测评分 + 引用完整性硬断言）。

### P2-2 后台执行器与韧性（修 D6/D15/D16）

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/chatbi/orchestration/runner.py` | 进程内 worker 池 + Run 队列 + 提交/关闭接口（保留升级外部队列的 port） |
| 新 | `backend/apps/chatbi/orchestration/reconciliation.py` | 启动对账：running 超时 Run → interrupted（可重跑/可恢复标记） |
| 迁 | `backend/alembic/versions/115_result_set_registry.py` [新] | `chatbi_result_set`（跨 Run 结果集引用，供追问增量计算） |
| 改 | `backend/apps/chatbi/orchestration/agent/service.py`、`api/interactions.py` | start=入队+订阅；SSE 只读事件流；断线重连走 after_sequence 补拉 |
| 改 | `backend/apps/chatbi/orchestration/agent/cancellation.py` | 内存信号注册表 + DB 兜底（替代 50ms 轮询） |
| 改 | `backend/apps/chatbi/orchestration/agent/{loop,lifecycle}.py`、`orchestration/pipeline/stages.py` | 事件提交批处理（降低每事件一 commit）；恢复入口 |
| 改 | `backend/apps/event/protocol/sse.py` | 心跳帧 |
| 改 | `backend/common/core/config.py` | runner 开关与并发 |
| 测 | `backend/tests/chatbi/test_runner_reconciliation.py` [新] + 断连恢复集成测试 | |

### P2-3 评测平台 + 运营闭环产品化（评测平台自 P0 下调至此）

**评测平台**（原 P0 规划的全量内容平移至此，结构不变）：建 `apps/evaluation`——评测集三表、分层 runner（understanding / binding / e2e）、结果集对比判分（列序无关/排序容差/4 位有效数字）、三连跑漂移检测、badcase 队列、趋势报表；种子自 `golden_cases.jsonl` 导入 + verified 同源转化；判分规则先与存量人工判定标定（§6.4），达标后启用 CI 门禁。

```
backend/apps/evaluation/                          [新]
├─ __init__.py / composition.py / errors.py
├─ models/orm/{evaluation.py, badcase.py}         eval_case / eval_run / eval_case_result + chatbi_badcase
├─ models/dto/evaluation.py
├─ repository/sqlmodel/evaluation_repository.py
├─ services/{case_loader, runner, scoring, drift, report}.py
├─ api/{evaluations, badcases}.py
└─ seeds/（golden_cases.jsonl 导入 + verified 同源）
```

| 操作 | 文件 | 说明 |
|------|------|------|
| 迁 | `backend/alembic/versions/{111_evaluation_platform,112_badcase_queue}.py` [新] | §1.2（编号执行时顺延） |
| 新 | `backend/apps/evaluation/` 全套（上图） | 平台本体 |
| 改 | `backend/apps/api.py` | 挂载 evaluation router |
| 新 | `backend/scripts/eval/run_eval.py`、`.github/workflows/eval-smoke.yml` | CLI 与 CI 门禁（标定通过后由 warn 转 block） |
| 改 | `backend/apps/chatbi/api/interactions.py` | `POST /record/{id}/feedback`（赞踩+原因）→ 踩自动入 badcase |
| 改 | 旧 `backend/scripts/evaluate_*` 脚本 | 标注"由 evaluation 平台替代，保留只读"；历史人工跑批报告导入平台作趋势基线 |
| 前 | `frontend/src/views/chat/`（answer 赞踩入口）+ 运营台页面（badcase 队列/晋升审核/评测趋势看板） | 前端新模块 |

**运营闭环**：

| 操作 | 文件 | 说明 |
|------|------|------|
| 新 | `backend/apps/knowledge/services/verified_promotion_service.py` | 成功 Run + 点赞 → verified 候选（从 AnalysisPlan 生成草案）；审核通过→verified 入检索与评测 |
| 新 | `backend/apps/knowledge/api/verified_queries.py` | 候选列表/通过/驳回 |
| 改 | `backend/apps/chatbi/orchestration/agent/lifecycle.py`（及 pipeline 收口） | 成功收口挂候选钩子 |
| 改 | `backend/apps/evaluation/services/runner.py` | badcase 处置后自动触发关联评测题回归 |
| 测 | `backend/tests/knowledge/test_verified_promotion.py`、`backend/tests/apps/evaluation/` [新] | |

### P2-4 权限 / 审计 / 限流 / 成本

| 操作 | 文件 | 说明 |
|------|------|------|
| 迁 | `116_semantic_asset_grant.py`、`117_usage_daily_and_quota.py`、`118_data_access_audit.py` [新] | §1.2 |
| 新 | `backend/apps/access_control/models/orm/asset_grant.py`、`services/asset_grant_service.py` | 角色→数据集内指标/维度可见性（语义侧消费） |
| 改 | `backend/apps/semantic/repository/sqlmodel/schema_loader.py` | 按 grant 过滤 DatasetSchema 资产 |
| 改 | `backend/apps/retrieval/sources/semantic_indexing.py` | acl_policy 角色化（P0-7 的完整版） |
| 改 | `backend/apps/datasource/services/query_service.py` | 列脱敏选项（mask 而非仅 deny）+ 数据访问审计事件（谁/何时/表/SQL 指纹/行数） |
| 改 | `backend/apps/access_control/`（data_policy 模型） | `ds_permission` 支持 mask 类型 |
| 改 | `backend/apps/api.py` | 恢复审计查询路由（现被注释停用） |
| 新 | `backend/common/middleware/rate_limit.py` | 令牌桶（用户 QPS/租户并发 Run/日配额），`backend/main.py` [改] 挂载 |
| 新 | `backend/apps/chatbi/services/usage_aggregation.py` | token/成本日聚合任务 + 报表端点（挂 `api/queries.py` [改]） |
| 测 | `backend/tests/access_control/test_asset_grant.py`、`backend/tests/datasource/test_column_masking.py`、`backend/tests/common/test_rate_limit.py` [新] | |

### P2-5 检索平台补全

| 操作 | 文件 | 说明 |
|------|------|------|
| 迁 | `119_knowledge_doc.py`、`120_retrieval_profile_override.py` [新] | |
| 新 | `backend/apps/knowledge/models/orm/knowledge_doc.py`、`services/knowledge_doc_service.py`、`api/knowledge_docs.py` | 业务知识文档上传/分块/管理 |
| 新 | `backend/apps/retrieval/sources/{knowledge_projector,knowledge_indexing}.py` | KNOWLEDGE_EVIDENCE 投影 |
| 新 | `backend/apps/retrieval/sources/schema_projector.py` | SCHEMA_FALLBACK 单元（ASSISTED 兜底的大库表级召回） |
| 新 | `backend/apps/retrieval/query/{knowledge_query,profile_overrides}.py` | 证据检索器；DB 阈值覆盖加载 |
| 改 | `backend/apps/retrieval/query/profiles.py` | 重新注册两 profile + 数据集级覆盖机制 |
| 改 | `backend/apps/chatbi/services/understanding/understanding_service.py`、`services/planning/plan_prompts.py`、`services/generation/fallback_sql.py` | 证据与 schema 召回消费 |
| 测 | `backend/tests/retrieval/{test_knowledge_profile,test_profile_overrides}.py` [新] | |

### P2-6 Graph 链路下线（条件触发：黄金集 ≥85% 且 Graph 场景覆盖清单核对通过）

| 操作 | 文件 | 说明 |
|------|------|------|
| 删 | `backend/apps/chatbi/orchestration/graph/` 全树（29 个文件，含 `api_extension.py`） | 主删除项 |
| 删 | `backend/apps/chatbi/services/understanding/{graph_contracts,intent_fallback,intent_projection,dimension_candidates}.py` 中 Graph 专用部分 | 逐一核对 Agent 侧无引用后删（`dimension_candidates` 供统一理解使用的部分保留） |
| 删 | `backend/apps/chatbi/adapters/` 中 Graph 专用适配（`sql_generation/dynamic_sql_generation/chart_generation/answer_*/analysis_prediction/datasource_selection/embedding_ranking/permission_sql_generation` 等） | 以"消费方仅 Graph"为删除判据，逐文件核对 |
| 删 | `backend/apps/chatbi/services/generation/` 中 Graph-era 服务（`sql_generation/dynamic_sql_generation/answer_generation/chart_generation/final_reply/analysis_prediction/permission_sql_generation` 及对应 context/）与 `services/planning/{datasource_candidates,datasource_selection}.py`（若仅 Graph 消费） | 同上判据 |
| 改 | `backend/apps/api.py`、`backend/sqlbot_platform/workflow_engine/`（chatbi 扩展注册处） | 摘除 WorkflowApiExtension 对 chatbi 的绑定（引擎本体是否保留由平台层决定，不在本计划范围） |
| 改 | `backend/apps/conversation/`（`chat_record.execution_type` 读写收敛为 agent；历史数据只读兼容） | |
| 迁 | `121_graph_sunset.py`（如需存量标记） [新] | |
| 删/改 | `backend/tests/{workflow,workflow_engine,chat}/` 相关用例；`backend/tests/architecture/` 守卫基线更新 | |
| 改 | 旧评测脚本 `evaluate_chatbi_first_phase.py`（Graph 链路）归档 | |

### P2 文件树图（增量）

```
backend/
├─ alembic/versions/{111,112,115..121}_*.py                  [新] §1.2
├─ main.py                                                   [改] P2-4 限流中间件
├─ apps/
│  ├─ api.py                                                 [改] P2-4 审计路由恢复 / P2-6 Graph 摘除
│  ├─ chatbi/
│  │  ├─ orchestration/{runner.py,reconciliation.py}         [新] P2-2
│  │  ├─ orchestration/pipeline/research.py                  [新] P2-1（占位→实装）
│  │  ├─ orchestration/agent/{cancellation,service,loop,lifecycle,budget}.py  [改] P2-1/2
│  │  ├─ orchestration/agent/tools/research.py               [新] P2-1
│  │  ├─ orchestration/graph/  ──────────── 全树              [删] P2-6
│  │  ├─ services/analysis/{attribution,report_composer}.py  [新] P2-1
│  │  ├─ services/usage_aggregation.py                       [新] P2-4
│  │  ├─ services/generation/{sql_generation,answer_generation,chart_generation,
│  │  │   dynamic_sql_generation,final_reply,analysis_prediction,…}  [删] P2-6（Graph-era）
│  │  ├─ services/understanding/{graph_contracts,intent_fallback,intent_projection}.py  [删] P2-6
│  │  └─ adapters/（Graph 专用适配群）                        [删] P2-6
│  ├─ access_control/{models/orm/asset_grant.py,services/asset_grant_service.py}  [新] P2-4
│  ├─ knowledge/
│  │  ├─ models/orm/knowledge_doc.py                         [新] P2-5
│  │  ├─ services/{verified_promotion_service,knowledge_doc_service}.py  [新] P2-3/5
│  │  └─ api/{verified_queries,knowledge_docs}.py            [新] P2-3/5
│  ├─ retrieval/
│  │  ├─ sources/{knowledge_projector,knowledge_indexing,schema_projector}.py  [新] P2-5
│  │  └─ query/{knowledge_query,profile_overrides}.py [新]、profiles.py [改]
│  ├─ evaluation/  ─ 全新 app（评测三表+badcase、runner/scoring/drift/report、api、seeds，自 P0 下调）  [新] P2-3
│  ├─ datasource/services/query_service.py                   [改] P2-4 脱敏+审计
│  ├─ event/protocol/sse.py                                  [改] P2-2 心跳
│  └─ conversation/                                          [改] P2-6 execution_type 收敛
├─ common/middleware/rate_limit.py                           [新] P2-4
├─ scripts/eval/run_eval.py                                  [新] P2-3
├─ sqlbot_platform/workflow_engine/                          [改] P2-6 摘除 chatbi 绑定
└─ tests/（access_control/datasource/common/knowledge/chatbi/apps/evaluation 新增用例；workflow* 清理；architecture 基线更新）
.github/workflows/eval-smoke.yml                             [新] P2-3
frontend/：运营台新模块、研究报告展示、答案赞踩入口            [前] P2-1/3
```

---

## 5. P3：增强（持续，方向级计划）

P3 各项启动前需按当时代码基线补充详细设计（预期形成 docs/tech/34+），此处给出范围与文件落点。

| # | 事项 | 主要落点 | 前置条件 |
|---|------|---------|---------|
| P3-1 | 深度研究完善：多假设管理、引用完整性硬校验、LLM-judge 观测评分 | `orchestration/pipeline/research.py` [改]、`services/analysis/` [扩]、`apps/evaluation/services/judge.py` [新] | P2-1 |
| P3-2 | 简单时序外推预测（评估后再做）：预测算子 + 图表预测段 + 免责标注 | `services/computation/forecast.py` [新]、`answer_composer/chart_spec.py` [改] | P1-4；产品评估通过 |
| P3-3 | 跨数据集路由：一次提问多语义模型自动选择（对标 Cortex `semantic_models[]`），会话解绑单数据集 | `orchestration/pipeline/dataset_router.py` [新]、`apps/retrieval/`（跨数据集 profile）[扩]、`apps/conversation/`（绑定模型调整 + 迁移 122）[改] | P2 稳定；多源需求确认 |
| P3-4 | AI 辅助建模冷启动：从 schema/查询日志起草语义资产 → 专家确认工作流 | `backend/apps/semantic/services/authoring/{draft_from_schema,draft_from_query_log,review_workflow}.py` [新]、`api/authoring.py` [新] | P1-10 instructions 就绪 |
| P3-5 | Apache Ossie 对齐：语义资产导出/导入（datasets/fields/relationships/metrics/ai_context + custom_extensions） | `backend/apps/semantic/services/interchange/{ossie_export,ossie_import}.py` [新]、`api/interchange.py` [新] | 语义模型稳定 |
| P3-6 | MCP 对外能力增强：计划级问数工具（提问→计划→结果集）暴露 | `backend/interfaces/mcp/` [扩] | P1-2/3 |
| P3-7 | 生成后自检（Genie Inspect 式）：对高价值查询生成更小验证 SQL 核实过滤值/时间窗/join | `backend/apps/chatbi/services/planning/inspect.py` [新]、`pipeline/stages.py` [改] | P1；成本评估 |

```
backend/apps/
├─ chatbi/{orchestration/pipeline/dataset_router.py, services/computation/forecast.py,
│          services/planning/inspect.py}                     [新] P3-2/3/7
├─ semantic/services/{authoring/, interchange/}              [新] P3-4/5
├─ evaluation/services/judge.py                              [新] P3-1
└─ interfaces/mcp/                                           [扩] P3-6
```

---

## 6. 测试与评测策略（跨阶段）

> 2026-08-16 调整：评测平台建设下调至 P2-3；P0–P1 期间评测为"脚本人工跑批"模式。

1. **单元/集成测试**（不受下调影响）：每工作项的 `[测]` 为 DoD 一部分；架构守卫（`backend/tests/architecture/`）在新增 `pipeline/`、`computation/`（P1）与 `evaluation/`（P2-3）包时同步更新依赖基线。
2. **P0–P1 评测方式（人工跑批）**：黄金题集维护在 `backend/scripts/golden_cases.jsonl`（版本化、随 PR 评审演进）；固定节奏——阶段末必跑 + 重大合并后必跑，报告沿用 fresh_20 样式、人工核验判分；P1 的 FAST/PLAN 与 react_legacy 等价性验证用脚本双跑对比（按 `execution_mode` 分组），不依赖平台。
3. **评测集演进**：P0 结束 ~50 题（现有 20+20 去重 + 值归一/澄清/L0 新题）；P1 结束 ~90 题（+对比/复合口径/多查询）；P2-3 导入评测平台后扩至 ~120 题（+归因/研究/权限场景），并与 verified query 同源双向转化。
4. **P2-3 平台化后**：判分规则（结果集对比）先在存量题集上与人工判定标定（误判率 <5% 才启用门禁）；CI 分层 = PR 门禁跑确定性套件（无外部模型）+ 夜间真模型全量 + 三连跑漂移；门禁初期仅 warn 两周再转 block；历史人工跑批报告导入平台作趋势基线。
5. **PR 门禁（P0–P1 过渡期）**：仅单元/集成测试 + lint；评测不进 CI。

## 7. 灰度、回滚与兼容策略

- 每项行为变更挂 §1.3 开关，回滚 = 关开关，不回滚代码；
- 数据迁移全部只增列/增表，不改不删存量列（Graph 下线的 121 除外，且其前置条件含数据兼容核对）；
- `react_legacy` 模式保留至 P1 验收后一个迭代；`derived_state` 旧结构（单槽 last_execution）在 P1 期间双写、P2 摘除；
- 事件契约只增不改（前端按 kind/phase 容错未知 domain）；
- verified/eval/badcase 三表独立于主链路，故障不影响问数。

## 8. 文档同步计划

| 时机 | 文档动作 |
|------|---------|
| P0 | docs/tech/23、25 增补勘误注记 |
| P1 | 新增 docs/tech/35-analysis-plan-and-modes.md（计划模型与三模式契约，含事件契约变更）；28（system prompt 基线）标注废弃范围 |
| P2 | 新增 docs/tech/34-evaluation-platform.md（评测平台使用说明，随 P2-3）与 docs/tech/36-operations-loop.md（运营闭环手册）；docs/tech/08/09/10（Graph 相关）标注已下线 |
| 持续 | backend/apps/AGENTS.md 补 pipeline/computation/evaluation 包的分层风格约定；COMPAT_LEDGER.md 记录每笔兼容与清偿 |

## 9. 风险与缓解（计划执行层面）

| 风险 | 缓解 |
|------|------|
| P0-1 澄清全覆盖工作量被低估（20 个 reason_code 的卡片语义各异） | catalog 允许 reason 先落"拒答+建议"最低档，卡片逐个补；验收标准是"零死局"而非"全部可澄清" |
| P1-2 编排重构与 legacy 并存期的双维护 | pipeline 复用 stages 服务层，legacy 只冻结不迭代；等价性测试兜底 |
| 编译器扩展（P1-6）触碰核心正确性 | golden SQL fixtures 先行（测试先写）；每口径独立模块独立开关 |
| DuckDB 类型/时区边界（P1-4） | 结果集 schema 显式类型映射表 + 边界单测（时区统一走 temporal 域产物） |
| 评测下调后 P0/P1 质量数字依赖人工跑批（判分未标定、无趋势追踪） | 固定节奏跑批（阶段末+重大合并后必跑）；题集 JSONL 随 PR 评审；P2-3 平台上线后导入历史报告回补趋势 |
| 评测判分误判造成门禁误伤（P2-3） | §6.4 标定流程；门禁初期仅 warn 两周再转 block |
| Graph 下线牵连隐蔽引用（P2-6） | 先静态引用扫描 + 运行期 deprecation 日志一个迭代，再删除 |
| 前端配合项排期失控 | `[前]` 项均为增量展示（事件容错），后端先行不阻塞；口径卡片/图表可分批上线 |
