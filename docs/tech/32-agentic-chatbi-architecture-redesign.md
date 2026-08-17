# 32. 智能问数（Agentic ChatBI）架构重设计方案

> 状态：设计评审稿 v1 ｜ 日期：2026-08-15 ｜ 基线：`codex/headless-dataset-chat` 工作区（含未提交改动）
> 输入：三路外部调研（国际商业产品 / 国内商业产品 / 开源与学术 / 语义层专题，来源见附录 A）+ 三份代码现状勘探（Agent 流程 / 语义与检索 / 支撑域）
> 范围：Agent 问数链路及其依赖的全部域。Graph/Workflow 链路不在设计范围内，仅在 §6.3 给出退役建议。
> 相关文档：现状基线 docs/tech/19/23/25/28；可执行语义模型 docs/tech/27；DDD 评审 docs/tech/12-14；记忆 docs/tech/29；观测 docs/tech/16/26
> 实施进度（2026-08-16）：P0 代码项、真实 PostgreSQL 迁移与 50 题真实 Agent 跑批已完成。针对首轮失败，已修复 Trace 参数、并发时间结果回投影、明细查询默认时间维度绑定、模型 JSON 一次受控修复、跨模型不兼容组合正常拒答，并修正评测脚本擅自代答澄清的问题。最终 43/50 `finished`、7/50 `waiting_user`、30/50 执行 SQL、43/50 产生答案、`run.failed=0`；按 `expected_points` 人工初审 40/50（80%），达到 P0 的 ≥65% 与死局为 0 目标。P1-1 至 P1-6 已完成首版：AnalysisPlan/ResultStore 契约、三模式路由、FAST/PLAN 管道、DuckDB ComputeEngine、comparison/composition/multi_step 意图和多区间时间计划，以及 derived/ratio、HAVING、时间偏移、预聚合、快照和关系契约安全校验已接入。旧单区间 TimeRange 投影继续保留；固定周期偏移渲染单 SQL，复杂偏移明确走现有双 QueryTask + ComputeTask 计划路径。默认仍保持 `react_legacy`，需通过灰度等价性验证后再切换；RESEARCH 尚未实现。仍有 6 条业务结果错误和 4 条多余澄清，作为后续质量改进项。
> 变更记录：2026-08-16 —— 按团队决策，评测平台建设下调为低优先级：从 P0 移至 P2（与运营闭环合并建设）；P0–P1 阶段验收改用现有脚本人工跑批。详见 docs/tech/33 v1.1。

---

## 0. 执行摘要

1. **现状判定**：当前 Agent 链路的能力上限是"一次问数 = 一条 SQL + 一次结果分析"。这不是模型能力问题，而是被架构写死的：一旦有执行结果，可见工具只剩 `finish`（`tool_visibility.py`）；结果槽位只有单个 `last_execution`（artifact 固定 `query-0`）；`TimeRange` 只有单区间，同比/环比在类型上就无法表达。同时存在三类"必然失败"死角（非 preflight 澄清死局、闲聊死代码、STRICT 零工具死角），最新 20 问全链路评测**严格业务正确率 47.4%**，发布判定不通过。
2. **调研结论**：企业级问数的差距不在 SQL 生成模型，而在四件事——**语义资产的治理、歧义的交互式消解、置信度分档与拒答、可回归的评测与反馈闭环**（Spider 2.0 / BIRD-Interact / 各商业产品共同证据）。语义层对准确率的提升有硬数据：仅 4KB 语义文档使三个前沿模型一致 +17~23pp（Cube 配对基准，p≤0.0015）。
3. **本项目的好消息**：经 DDD R0-R6 与 doc 27 落地，语义资产存储模型（含指标口径、粒度、可加性、join 契约、业务身份三表）与统一检索平台（混合召回 + 确定性门控）**已达到调研所见的先进形态，应整体保留**；权限三道闸、temporal 域、工具运行时、事件/Trace 双轨也是合格资产。**断层集中在运行时**：值映射不参与查询、派生指标/预聚合"有契约无编译"、SQL 示例不进主路径、检索 ACL 未接通，以及编排层把模型压成"按按钮"的伪 ReAct。
4. **目标架构**：以 **AnalysisPlan（分析计划）** 取代"单 SQL 状态机"作为一次问数的核心抽象——计划是由 QueryTask（语义查询，确定性编译执行）与 ComputeTask（跨结果集确定性计算）组成的小型 DAG，配 **ResultStore（命名结果集）**。编排收敛为三种模式：**FAST**（简单问题，零规划 LLM，理解→绑定→单节点计划→编译执行→作答）、**PLAN**（复杂查询，规划 LLM 一次产出计划，逐节点确定性校验执行）、**RESEARCH**（归因/开放"为什么"，有界 agentic 循环产出带引用的分析报告）。
5. **能力边界**（§2）：企业可用为标准，不追求超越头部商业产品。P1 结束时覆盖 L0-L3（会话/单查询/分组筛选/对比同环比/复合口径），P2 覆盖 L4（下钻/归因/跨查询计算）并提供 L5（深度研究）first-cut；明确不做 what-if、写操作、全库自由问答。
6. **语义层结论**（用户问题 4 的直接回答）：**存储模型基本满足方案，不需要推倒**；需要的是 8 项补课——值归一链路、派生/比率指标编译、预聚合与快照渲染、指标级 filter 生效、verified query 资产化与晋升闭环、模块化 instructions、检索 ACL 接通、以 capability 契约统一检索与编译的兼容性事实源。
7. **确定性优先**：遵循行业共识"能编译的绝不生成、能结构化的绝不写提示词"。标准路径 LLM 只负责理解/规划/作答三处；SQL 由语义编译器产出；跨结果集计算由计算引擎（DuckDB）确定性执行，禁止 LLM 心算。
8. **置信度四档路由**成为一等机制：高置信直答；中置信直答+口径披露；歧义→澄清（所有 reason_code 均可澄清，修复 D2）；语义层外/低置信→按数据集策略走"受控 NL2SQL 兜底（标注非认证口径）"或拒答。
9. **评测升级为平台能力**：评测集与 verified queries 同源，结果集对比判分，CI 回归门禁；质量目标：黄金集严格正确率 P0 ≥65% → P1 ≥80% → P2 ≥85%，死局类失败清零，回答数字 100% 可溯源。（2026-08-16 调整：平台建设移至 P2，P0–P1 以脚本人工跑批过渡，质量目标不变。）
10. **分阶段落地**（§7）：P0 止血与地基（修死局、值归一、示例进主路径、评测 CI）→ P1 复杂查询能力（AnalysisPlan/ResultStore/ComputeEngine、派生指标编译、双模式收敛）→ P2 企业化运营（RESEARCH 模式、后台执行器、运营闭环、权限/成本/审计）→ P3 增强（预测、跨数据集、AI 辅助建模、Ossie 对齐）。

---

## 1. 外部调研结论

四份完整调研报告的来源清单见附录 A，此处只保留对设计有直接约束力的结论。

### 1.1 商业产品共识（国际 8 家 + 国内 12 家）

**标配能力（所有头部产品一致，本方案的 MUST）**

| # | 共识 | 代表实现 |
|---|------|---------|
| 1 | **策展式作用域**：没有一家做"全库随便问"，问答单元是精心圈定的数据集/主题 | Genie ≤30 表（建议 ≤5）、Amazon Quick ≤12 数据集、Looker agent ≤5 Explores |
| 2 | **六件套语义上下文**：表列描述（给 AI 与给人可分开）、同义词、结构化指标口径（能用 SQL/结构表达绝不写文本）、显式 join 关系、示例值/字面值检索、按模块拆分的文本指令 | Snowflake semantic view YAML、Databricks metric views、Quick BI 知识库 |
| 3 | **认证答案优先路由**：verified query 命中 > 语义生成 > 兜底 | Snowflake VQR、Genie trusted assets、PBI verified answers、腾讯云"标记正确即命中返回" |
| 4 | **多轮 + 追问改写为自包含问题**；澄清触发条件可由作者用指令定义 | Cortex Analyst、Genie |
| 5 | **过程透明**：口径/SQL/取数逻辑可查，"业务逻辑表达 + 物理 SQL"双层透出 | Quick BI、PBI HCAAT、网易"可信四原则" |
| 6 | **权限贯穿而非复制**：查询在终端用户数据权限下执行，作用域不是安全边界 | 全部产品一致，Databricks/Google 有原话级声明 |
| 7 | **反馈闭环 + 运营台**：赞踩→badcase 队列→处置→复用 | Quick BI 问数运营、Genie Monitor + 周报 AI 分析 |
| 8 | **评测是产品功能**：基准题集 + 执行结果集对比判分 + 回归追踪 | Genie 500 题 Benchmark、Snowflake leave-out 评测 |

**拉开差距的高级能力（本方案的选做与竞争点）**：确定性查询编译（ThoughtSpot 全量如此）；语义 SQL 优先/标准 SQL 兜底的两段式（Snowflake Routing Mode）；生成后写更小验证 SQL 主动核实的自检（Genie Inspect）；快问快答与深度研究双档（Genie Agent mode / Spotter Research）；把纠错沉淀为永久资产的记忆（ThoughtSpot Rules+Recipes）；AI 辅助建模冷启动。

**国内落地共识**：没有一家生产可用产品是"裸 LLM 直连物理表"；路线演化 NL2SQL → NL2DSL → 明细级语义层（NL2MQL）；**确定性 > 覆盖率**是普遍选择（哈啰 DSL 拒答 30% 换命中后近 100% 准确；腾讯云"可信/专业"双模式）；帆软"可累加/半累加/不可累加"指标属性是防口径错误的建模级创新（本项目 `additivity` 字段已具备）；冷启动是项目问题不是模型问题（选 1-2 个场景→配齐语义→试点培训→运营迭代）。

### 1.2 开源与学术共识

- **生成层六件套**（BIRD 头部方法共性）：schema 表示与压缩（M-Schema）、值检索、多候选生成、候选选择、执行反馈自修正、动态 few-shot。生成层可做到 75-80%（BIRD），但 Spider 2.0（企业级）单发模式榜首仅 76 / 项目级 65——**企业可用性的缺口不在生成层**。
- **编排共识是混合架构**：固定阶段骨架（可控、可观测、可评测）+ 特定阶段内有界 agentic 子循环（schema/值探索、错误修复，设步数预算）；按问题难度分级算力。纯 ReAct 与纯 pipeline 都不是答案。
- **置信度**：多候选执行一致率是预测 SQL 正确性最强信号；四档路由（直答/警示/澄清/拒答）；**拒答能力是企业可用性的一部分**（TrustSQL）。
- **schema linking 的工程口径**：小库不过滤宁多勿漏；大库两段式（宽松粗召回保 recall + 生成时保留完整列描述）；把它当召回问题而非精确分类问题。
- **模糊问题**：歧义即使给全上下文也无法单方消解（AMBROSIA），必须内建"识别歧义→低成本选项式澄清"，BIRD-Interact 证明交互本身是可靠性机制。
- **上游 SQLBot 基线定位**：Vanna 式 RAG 三件套（术语/SQL 示例/自定义 prompt）+ M-Schema 拼 prompt 的单类流水线；无语义建模层、无多候选、无置信度、无自动回流——本项目已远超上游，但上游的工作空间隔离/行列权限/MCP 嵌入是成熟资产（已继承）。

### 1.3 语义层设计共识

- **硬数据**：语义文档使三个前沿模型 +17~23pp 且"语义层的有无解释几乎全部方差，换模型不解释"（arXiv:2604.25149）；AtScale 经语义层 92.5%（TPC-DS 40 题）；BIRD external-knowledge 消融 ≈ +20pp。
- **五大规范（dbt/Cube/LookML/Snowflake/Databricks）已趋同**为四层资产骨架：结构层（逻辑表/关系基数/时态 join）→ 语义层（维度/事实/度量+可加性/派生指标/默认过滤）→ AI 增强层（同义词/枚举与字面量检索/verified queries/模块化 instructions）→ 治理层（权限/认证标记/owner/版本）。
- **消费两路线**：(a) LLM 生成语义查询，引擎编译物理 SQL——正确性、join 规划、fan-out 防护、权限注入都在编译期强制（Cube/dbt/Databricks/Looker）；(b) 语义模型作 grounding、LLM 直接生成物理 SQL（Cortex Analyst）。企业级建议 (a) 为主 (b) 为兜底——**本项目 STRICT 编译链路已经就是路线 (a)，方向正确**。
- **生命周期闭环**：AI 起草+专家确认冷启动；运行期"用量→建议 verified query→人工认证→从认证 SQL 泛化语义概念回填"；确定性评测集做合并门禁。
- **Apache Ossie**（原 OSI，2026-07 进 Apache 孵化器）：开放语义交换标准把 `ai_context`（instructions/synonyms/examples）写进规范；自建 schema 应保持可映射。

### 1.4 十条设计公理（本方案全部决策的依据）

1. 语义层是准确率的第一杠杆；表结构+提示词不是企业级路线。
2. 确定性 > 覆盖率：能编译的绝不生成，能结构化的绝不写进 prompt。
3. 收窄作用域：数据集是问答单元，不做全库自由问答。
4. 认证资产优先：verified query 命中 > 语义编译 > 受控 NL2SQL 兜底。
5. 混合编排：确定性阶段骨架 + 有界 agentic 子循环，按难度分级算力。
6. 澄清与拒答是能力不是缺陷：置信度四档路由是一等机制。
7. 权限只信执行引擎：编译期/执行期强制，作用域与 prompt 都不是安全边界。
8. 全过程透明：口径、SQL、数据引用用户可查，回答数字必须可溯源。
9. 评测是产品功能：评测集与 verified query 同源，改动必须过回归门禁。
10. 系统必须越用越准：badcase→资产修复→回归验证的运营闭环从第一天建。

---

## 2. 能力边界定义

### 2.1 定位

面向企业内业务用户与分析人员的**对话式取数与分析**系统：在管理员圈定并治理过的数据集范围内，以自然语言完成从取数、对比、下钻到归因解读的分析闭环，全程口径可信、权限受控、过程可查。**不是**通用 SQL 助手，**不是**数据科学平台。

### 2.2 支持的问题类型分级

| 级别 | 类型 | 示例 | 关键依赖 | 落地阶段 |
|------|------|------|---------|---------|
| **L0 会话与元问题** | 闲聊、能力询问、资产目录（"你能查什么""有哪些指标"） | "你好""销售额是怎么定义的？" | 问题分类 + 语义资产目录接口 | P0（修复 D3 死代码） |
| **L1 单查询** | 指标值、聚合、明细、筛选（含值归一）、排序、LIMIT | "本月华东区销售额""昨天客单价前 10 的门店" | 现有 STRICT 链路 + VALUE 槽 | P0 |
| **L2 分组与结构** | 多维分组、时间粒度、占比、排名、去重计数、跨表（语义 join） | "各品类销售额按月""华东占全国比重" | 语义编译器（已有）+ 占比计算 | P0-P1 |
| **L3 对比与复合口径** | 同比/环比、任意双时段对比、增长率、多指标并列、比率/派生指标、累计（YTD）、滚动窗口、TopN+其他 | "本月 vs 上月各区销售额及增幅""毛利率同比" | **AnalysisPlan 多查询 + ComputeEngine + 派生指标编译** | **P1（本方案核心增量）** |
| **L4 多步分析** | 下钻（总→分维）、变化归因（维度贡献分解）、异常识别、跨查询计算 | "销售额为什么降了，哪个区拖累的？"（模板化归因） | PLAN 模式 + 归因模板 + 结果集计算 | P2 |
| **L5 深度研究** | 开放式"为什么"、多假设迭代、带引用的分析报告 | "分析 Q2 客单价下滑的原因并给建议" | RESEARCH 模式（有界 agentic 循环） | P2 first-cut，P3 完善 |

**多轮能力**（贯穿各级）：追问改写为自包含问题（已有）；计划级增量修改（换时间/换维度/加筛选直接 patch 上一个 AnalysisPlan，不重走全流程，P1）；澄清后精确恢复（已有，扩展到全部歧义类型）。

### 2.3 明确不做（拒答并说明理由/建议）

- 预测建模与 what-if 模拟（P3 仅评估"简单时序外推"作为可选增强，学商业产品普遍做浅）；
- 任何写操作（只读是执行层硬约束，保持现状）；
- 跨数据源 join（P3 评估；当前单 Run 单数据源绑定保留）；
- 绕过语义层的全库自由问答（未配语义资产的表只能走受控兜底且明确标注，或按数据集策略拒答）；
- 非授权数据（权限三道闸，保持现状）。

### 2.4 质量目标（发布门禁指标）

| 指标 | 现状 | P0 | P1 | P2 |
|------|------|----|----|----|
| 黄金集严格业务正确率（执行结果对比判分） | 47.4%（fresh_20） | ≥65% | ≥80%（含对比/复合口径新题） | ≥85% |
| 死局类失败（澄清死局/零工具/finish 死循环） | 存在（D2/D3/D4/D7） | **= 0** | = 0 | = 0 |
| 回答数字可溯源率（引用绑定校验） | 无机制 | — | 100% | 100% |
| 模糊问题合理处置率（澄清或拒答而非错答） | 低（多数失败） | ≥80% | ≥90% | ≥95% |
| FAST 路径 LLM 调用次数 | 5-7 次（ReAct 每步一次） | — | ≤4 次 | ≤4 次 |
| 同题三连跑结果一致率（漂移检测） | 有漂移记录（N17） | 纳入观测 | ≥90% | ≥95% |
| 评测执行方式 | 手工脚本 | 手工脚本跑批（平台下调至 P2） | 手工跑批 + 题集扩充 | 平台化：CI 门禁 + 夜间全量 + 线上抽样回流 |

---

## 3. 现状评估

### 3.1 值得保留的资产（重设计的地基，不重造）

| 资产 | 位置 | 评价 |
|------|------|------|
| 语义资产存储模型 | `backend/apps/semantic/models/orm/`（17 表 + 3 契约表） | 含指标口径/粒度/可加性/join 契约（cardinality、aggregation_safety、metric_propagation）/业务实体三表，达到调研所见规范（Snowflake SV / MetricFlow）同代水平 |
| 语义编译器与 PROVEN 计划链 | `semantic/services/sql_compiler.py`、`services/query/planning.py`、`validation.py` | "只有 PROVEN 计划才可编译 + 指纹校验"即行业路线 (a)，正确方向；缺口在表达力（§5.1） |
| 统一检索平台 | `backend/apps/retrieval/`（7 表 + generation 索引 + 混合召回 + RRF + 确定性门控） | 工程质量高：exact/alias fast-path、证据分层、指标-维度兼容收敛、DEGRADED 不掩盖歧义 |
| 权限三道闸 | 检索时过滤 → schema 裁剪 → `datasource/services/query_service.py` 执行前 sqlglot 行列权限重写 | 扎实，validate/execute 不信任上游各自全量复验 |
| temporal 域 | `backend/apps/temporal/` | Run 级冻结 TemporalContext + 确定性解析 + 统一 SQL 渲染，业界少见的认真做法 |
| 工具运行时 | `backend/apps/tool/`（白名单/可信参数投影/预算熔断/并行批次） | 契约清晰，保留为执行基础设施 |
| 事件/Trace 双轨 | `backend/apps/event/`、`apps/trace/` + `chatbi_agent_trace_node` | 契约成型，补拉可用；OTEL 已接默认关 |
| 问题理解（2 次调用：重写+统一理解） | `chatbi/services/understanding/` | 结构合理，输出 DTO 可作为规划层输入；需扩展意图类型与分类 |
| 记忆域 | `backend/apps/memory/`（L1/L2/L3 分层 + 灰度评测框架） | 机制齐全数据为零，激活即可 |
| 会话域与澄清挂起/恢复、取消状态机 | `apps/conversation/`、`chatbi/orchestration/agent/cancellation.py` 等 | 保留 |

### 3.2 六大结构性缺陷（重设计要解决的问题）

以下将三份勘探报告的 50+ 条问题归纳为六类（括号内为勘探报告编号，file:line 证据见 docs/tech/25 勘探与本方案输入报告）。

**缺陷一：编排模型把系统锁死在"单查询"（D1/D2/D3/D4/D7/D9）**
- `tool_visibility.py` 状态机：有执行结果→只剩 `finish`，物理上不可能第二条查询；结果槽位单个 `last_execution`、artifact 硬编码 `query-0`；`TimeRange` 单区间，同环比在类型上不可表达；检索层已产出 `multi_query_plans` 但 Agent 路径零消费。
- 三类必然失败：非 preflight 澄清（约 17/20 个 reason_code）进入循环即零工具死局；闲聊判定读不存在的字段（死代码）；STRICT 非 PROVEN 即零工具。
- ReAct 形式与实质脱节：compile 只收服务端指纹、SQL 被整体替换、clarify 选项被重建、finish 无参——模型唯一自由度是"何时按哪个按钮"，却为每步支付一次 LLM 调用与延迟。

**缺陷二：查询表达力不足（语义勘探 A 组）**
- 派生/比率指标：`metric_refs`/`expr` 已存储但规划只取第一个 measure，编译器无 ratio/同环比/占比语义；
- 预聚合契约"有验无实"：能力表可声明 `PRE_AGGREGATE_REQUIRED`，校验会查，编译器完全没有预聚合子查询渲染；快照语义（期末取值）同样只到校验层；
- 指标级 `filter_sql` 无编译消费点；严格计划无 HAVING；join 路径贪心求解不使用契约的 cardinality/relation_path。

**缺陷三：运行时语义闭环断层（语义勘探 C/D 组）**
- **值映射断裂**：维值已入库已入索引（含 canonical_value），但查询规划器从不生成 VALUE 槽，过滤值直接拿用户原话进 SQL（"华东"不会变成 `region='EAST'`）；
- SQL 示例（verified query 雏形）不进 STRICT 主路径，无自动沉淀，无晋升工作流；
- 术语弱关联（不进编译白名单）；业务知识文档检索（KNOWLEDGE_EVIDENCE profile）注册即空转；
- 检索 ACL 机制存在但请求侧不传（资产可见性退化为租户级）。

**缺陷四：回答与结果处理粗放（D11/D17）**
- finalization 把全量结果行塞进两次 LLM，行数仅靠默认 LIMIT 约束；图表仅 4 种、单 x 单 y 系列；无引用绑定（回答数字与结果集无校验关系）；observation 超 2000 字符整体腰斩为非法 JSON。

**缺陷五：工程韧性不足（D6/D14/D15/D16 + 支撑域 7）**
- Agent 在 SSE 生成器内同步执行，断连即 Run 永久 running，无对账恢复；取消靠 50ms DB 轮询；每事件一次 commit；`CACHE_TYPE=memory` 多实例不一致；无限流/配额/成本核算。

**缺陷六：质量与运营体系缺位（支撑域 4/8/9/10）**
- 评测全部是一次性脚本，无 CI 门禁、无结果库、无趋势；严格正确率 47.4% 无法量化追踪修复；badcase 无队列；记忆/灰度/OTEL 全部默认关、数据为零；提示词硬编码无版本；审计查询 API 已注释停用。

### 3.3 结论

架构骨架（DDD 边界、语义存储、检索平台、权限、观测契约）**不需要推倒重来**；需要重造的是**编排层**（从单查询状态机到计划驱动的三模式）、需要补课的是**语义运行时闭环**与**评测运营体系**。这与调研结论精确对应：企业可用性的缺口在治理、交互消解、置信路由、反馈闭环——恰好是当前最薄弱的四处。

---

## 4. 目标架构总览

### 4.1 分层视图

```
接入层    Web / 嵌入 / MCP / OpenAPI
────────────────────────────────────────────────────────────
编排层    RunOrchestrator —— 三种执行模式：FAST / PLAN / RESEARCH
          ├ ① 分诊与理解   问题分类(L0/问数/越界) → 重写 → 统一理解 → 置信评估
          ├ ② 语义绑定     检索平台：指标/维度/值/术语/示例/知识 → 绑定决策/澄清
          ├ ③ 规划         AnalysisPlanner → AnalysisPlan（QueryTask/ComputeTask DAG）
          ├ ④ 校验与路由   计划校验(PROVEN) + 置信度四档：直答/披露/澄清/拒答
          ├ ⑤ 执行         QueryExecutor（并行 DAG）→ ResultStore（命名结果集）
          ├ ⑥ 计算         ComputeEngine（DuckDB，跨结果集确定性计算）
          └ ⑦ 回答         AnswerComposer（结论 + 引用绑定 + 图表 spec + 追问建议）
────────────────────────────────────────────────────────────
语义层    语义资产（模型/指标/维度/维值/契约）+ 语义编译器 + verified queries
          + instructions（模块化指令）+ 资产目录接口
检索层    统一检索平台（+VALUE 槽 / exemplar / knowledge / ACL / trace 持久化）
支撑域    temporal ｜ memory ｜ access_control ｜ tool runtime ｜ event+trace
          ｜ 评测平台 ｜ 运营台（badcase/晋升/看板）
```

与现状的关系：①②沿用现有理解与检索服务（扩展）；③⑤⑥⑦是新建；④整合现有 PROVEN 校验并新增置信路由；`apps/tool` 降级为执行基础设施（编排不再依赖"工具可见性"驱动流程）。

### 4.2 核心抽象

**AnalysisPlan（一次问数的执行合同，替代"单条 SQL"）**

```jsonc
{
  "plan_id": "…", "mode": "fast | plan | research",
  "question": "本月 vs 上月各区域销售额及增幅",
  "nodes": [
    { "id": "q1", "type": "query",              // QueryTask：语义查询规格，不是 SQL
      "spec": { "metrics": ["metric:sales_amount"],
                "dimensions": ["dim:region"],
                "filters": [], "time_range": {"normalized": "2026-08-01~2026-08-15"},
                "order": [], "limit": 1000 },
      "compiled": { "plan_fingerprint": "…", "sql": "…" } },   // 由语义编译器回填
    { "id": "q2", "type": "query", "spec": { "…": "上月同口径" } },
    { "id": "c1", "type": "compute",            // ComputeTask：跨结果集确定性计算
      "op": "compare",                          // compare|growth|share|topn_other|pivot|expr
      "inputs": ["q1", "q2"], "join_on": ["region"],
      "derive": [{"name": "增幅", "expr": "(q1.sales - q2.sales) / q2.sales"}] }
  ],
  "edges": [["q1","c1"], ["q2","c1"]],
  "presentation": { "primary": "c1", "chart_hint": "grouped_bar" },
  "validation": { "status": "PROVEN", "reports": {"q1": "…", "q2": "…"} }
}
```

设计要点：
- **QueryTask.spec 即现有 `SemanticQueryPlan` 的超集**——复用 doc 27 的规划/校验/编译链，每个节点独立走 PROVEN 验证与指纹校验，安全性质不变；
- **ComputeTask 是受限操作集**（P1：compare/growth/share/topn_other/pivot/expr 白名单表达式），由 ComputeEngine 在 DuckDB 中对命名结果集确定性执行，**LLM 不做算术**；
- 简单问题的计划是"单 QueryTask 节点"，由绑定结果**规则直出**（不经规划 LLM）——FAST 模式因此比现状更省调用；
- 计划整体有指纹与校验报告，是澄清恢复、重试、审计、评测回放的统一载体。

**ResultStore（命名结果集）**

- 每个 QueryTask/ComputeTask 产出一个命名结果集（r-q1、r-c1…），持久化为 Run Artifact（复用现有 Artifact 网关），元数据含 schema、行数、来源 SQL、口径引用；
- 替代单槽 `last_execution` / 硬编码 `query-0`；回答与图表通过**结果集引用**取数；多轮追问可引用上一 Run 的结果集做增量计算（P2）；
- 进 LLM 上下文的永远是"schema + 统计摘要 + 采样行"，全量行只进 ComputeEngine 与前端表格（修复 D17）。

### 4.3 三种执行模式与选择规则

| 模式 | 触发 | LLM 调用 | 流程 |
|------|------|---------|------|
| **FAST** | L0-L2 且绑定 RESOLVED 且无复合计算 | ≤4 次（重写、理解、作答；分类并入理解） | 理解→绑定→规则直出单节点计划→编译→执行→作答 |
| **PLAN** | L3-L4：对比/多时段/复合口径/下钻/归因模板，或用户显式多步意图 | +1 次规划调用（结构化输出 AnalysisPlan 草案，服务端校验/修复，最多重试 1 次） | 理解→绑定→规划 LLM 产出计划→逐节点 PROVEN 校验→DAG 并行执行→计算→作答 |
| **RESEARCH** | L5：开放"为什么"/用户显式"深入分析"，数据集开启该能力 | 有界循环（预算：默认 ≤8 次查询、≤15 次 LLM、≤300s，可配） | 研究 agent 在"提出子问题→生成 QueryTask→看结果摘要→决定下一步"循环中工作；每个查询仍走绑定/校验/编译全链；产出带引用的结构化报告 |

模式选择是**确定性规则**（意图类型 + query_shape + 数据集配置），不是 LLM 决定；用户可显式升级（"深入分析一下"）。RESEARCH 是唯一保留自由 agentic 循环的地方——这正是调研共识"有界 agentic 子循环"的落点，把 agentic 的成本花在它有回报的问题上（修复 D9 的成本错配）。

### 4.4 一次问数的生命周期（PLAN 模式示例）

```
用户提问 → 创建 Record/Run（沿用）
① 分诊+理解：分类(chitchat→直答退出 / 越界→拒答 / 问数→继续)
   重写 → 统一理解 → 输出 QuestionUnderstanding（扩展 comparison/multi_step 意图）
② 语义绑定：RetrievalRequest（+VALUE 槽 +exemplar +instructions）
   → RESOLVED / AMBIGUOUS(→澄清卡片，全部 reason_code 可澄清) / MISSED(→四档路由)
③ 规划：规划 LLM 收到 理解+绑定资产+verified query 命中+instructions
   → AnalysisPlan 草案 → 服务端逐节点语义校验(PROVEN) → 校验失败带原因重试一次
④ 路由：置信度 = f(绑定证据等级, 校验状态, verified 命中, 兜底与否)
   高→直接执行；中→执行+口径披露；歧义→clarify；低/超界→拒答或受控兜底
⑤ 执行：DAG 依赖序并行执行 QueryTask（每 SQL 过三道闸）→ ResultStore
⑥ 计算：ComputeEngine 执行 ComputeTask → 派生结果集
⑦ 作答：AnswerComposer —— 结论(引用绑定) + 口径卡片(用了哪些指标/过滤/时间)
   + 图表 spec + 追问建议；数字溯源校验不通过则降级为表格直出
事件流：plan-created / task-started / task-finished(result-set ref) / compute-finished
        沿用 kind×phase 两轴契约扩展 domain
```

失败路径：任何节点执行失败 → 结构化错误回注 → 修复子循环（沿用 SQL 修正预算 ≤2）→ 仍失败则**部分作答**（已成功节点的结果 + 失败说明），不再整体死局（修复 D7）。

---

## 5. 分域设计

每域按"现状 → 目标 → 变更清单（含阶段标记）"组织。

### 5.1 语义资产层（用户问题 4 的完整回答）

**结论：存储模型满足方案骨架，无需推倒；按四层资产模型对标，缺口集中在"运行时可用性"而非"存储表达力"。**

对标四层资产骨架的差距评估：

| 层 | 已具备（保留） | 缺口（变更） |
|----|--------------|-------------|
| 结构层 | 模型/字段/度量、join 关系含 cardinality/aggregation_safety/valid_time_condition | 编译器不消费契约选 join 路径（P1）；层级(hierarchy)未建模，下钻靠维度关系推断（P2） |
| 语义层 | 指标口径（expr/agg/filter_sql/result_grain/**additivity**/time_semantics/snapshot_aggregation）、维度/时间维/维值、模型级 filter_sql | ①派生/比率指标编译（P1）②预聚合渲染（P1）③快照渲染（P1）④指标级 filter_sql 生效（P0）⑤HAVING（P1）⑥数据集级默认过滤（P1） |
| AI 增强层 | 别名/术语/维值别名、SQL 示例表（verification_status 已有） | ①VALUE 槽值归一链路（P0）②verified query 资产化+晋升闭环（P0 表结构/P2 闭环）③模块化 instructions 资产（P1）④业务知识文档（P2）⑤示例问题=评测集同源（P0） |
| 治理层 | sensitive_level、契约版本、发布校验 | ①检索 ACL 接通（P0）②资产级授权（角色→指标/维度可见性，P2）③认证/废弃标记参与排序（P1）④变更影响分析（P3） |

**具体变更**

1. **值归一链路（P0，最高性价比）**：理解输出的 `dimension_filter values` → 检索规划器生成 VALUE 槽 → 命中 canonical_value 则替换、多命中走澄清、未命中按数据集策略（保留原值+低置信 或 澄清）。补两个配套：`value_query_sql` 的定时同步任务（维值从业务库自动刷新+频次统计，P1）；`headless_asset_alias` 统一别名表接入召回排序（P1）。
2. **指标表达力补课（P1）**：编译器支持 `metric_refs` 展开（derived）、ratio（分子/分母子查询对齐维度后相除）、时间偏移（同环比的单 SQL 形态，复杂时段自动降级为双 QueryTask+compare）、`PRE_AGGREGATE_REQUIRED` 的预聚合子查询渲染、`snapshot_aggregation` 的期末/期初取值渲染、HAVING。**验收方式：每类口径进评测集。**
3. **verified query 一等资产化（P0 起步）**：`data_training` 升级为 `knowledge.verified_query`：question、语义计划（逻辑引用而非裸 SQL，可回放）、sql（编译产物缓存）、verified_by/verified_at、source（manual / promoted_from_run）、status（candidate/verified/deprecated）、use_as_onboarding。消费点：检索命中相似 verified→注入规划上下文并提升置信（P0）；高相似度直接复放计划（P1，即"认证答案优先路由"）；同源生成评测集（P0）。
4. **模块化 instructions（P1）**：新增数据集级指令资产，分 `sql_generation`（口径规则：财年、默认过滤、单位）与 `question_categorization`（何时澄清/拒答、话题边界）两模块（对标 Snowflake `module_custom_instructions`），进入理解与规划 prompt 的固定槽位，替代散落的自定义 prompt。
5. **统一兼容性事实源（P1，化解勘探指出的 G23 架构张力）**：检索层 `compatible_dimension_ids` 宽口径启发式逐步退位，检索决策与编译校验共同以 `headless_metric_dimension_capability` 契约为唯一事实源；当前工作区未提交的 policy 收敛改动作为过渡保留，capability 覆盖率达标的数据集切换到契约判定。
6. **STRICT 推广与 LEGACY 退役（P1-P2）**：逐数据集完成契约 backfill（`contract_backfill.py` 已有规划工具）→ 默认 `semanticEnforcement: STRICT` → 删除编译器文本匹配 `_match_elements` 与候选表修复 `_repair_element_by_candidate_tables`（doc 27 明确要求）。
7. **兜底通道语义**（P1）：数据集三态 `STRICT`（仅认证口径，语义层外拒答）/ `ASSISTED`（语义优先，受控 NL2SQL 兜底：全 schema+示例上下文生成、三道闸校验、回答强制标注"非认证口径"与置信提示）/ `LEGACY`（仅迁移期）。对标腾讯云可信/专业双模式与 Snowflake Routing Mode。
8. **Ossie 对齐（P3）**：语义资产导出/导入 Apache Ossie 格式（datasets/fields/relationships/metrics/ai_context），私有字段走 custom_extensions；作为对外集成与防锁定投资。

### 5.2 问题理解

**现状**：2 次调用（重写+统一理解）结构合理；意图 8 类；输出 DTO 是全流程事实源；正在进行的未提交改动（统一单遍理解、多轮维度继承）方向正确。

**变更**
1. **分诊前置（P0）**：统一理解增加 `category` 输出（chitchat / data_query / meta_query / out_of_scope），修复 D3 死代码；chitchat/meta 直答退出（meta 走语义资产目录接口，回答"有哪些指标/维度"）；out_of_scope 拒答带理由。
2. **意图扩展（P1）**：`intent_type` 增加 comparison（多时段/多组对比）、composition（占比）、multi_step（下钻/归因）；`TimeRange` → `time_ranges[]` + `ComparisonSpec{base, compare[], method: yoy|mom|custom}`；`query_shape` 相应扩展。这是规划层的输入契约。
3. **澄清全覆盖（P0，修复 D2）**：删除"validation ≠ valid → 零工具"逻辑；所有 reason_code 映射为可澄清项（intent_unknown→意图选项、metric_missing→指标候选、ranking_*→排名口径……），统一走现有澄清卡片机制；超出澄清预算或用户拒绝澄清→四档路由的拒答分支（带建议问法）。
4. **置信输出（P1）**：理解输出附 confidence 与依据（继承自绑定证据等级），供路由使用。
5. 清理：understanding 模块内 Graph 专用双轨（INTENT/DIMENSION prompts 等）随 Graph 退役删除（§6.3）。

### 5.3 检索与语义绑定

**现状**：平台保留；semantic_binding 主 profile 的门控/歧义/联合约束是高质量资产。

**变更**
1. VALUE 槽规划与值归一（P0，见 5.1.1）。
2. exemplar 进主路径（P0）：`RetrievalBundle.exemplars` 填充，verified query 相似命中注入规划上下文；STRICT 路径同样消费（当前仅 LEGACY 提示使用）。
3. KNOWLEDGE_EVIDENCE profile 落地（P2）：业务知识文档（口径说明/业务规则/分析方法）分块入索引，理解与规划阶段按需召回；在此之前从 profiles 注册表移除空转项（P0，诚实性）。
4. 检索 ACL 接通（P0）：Agent 构造 `RetrievalRequest` 时传 principal/roles/permission_version（`chatbi/orchestration/agent/tools/base.py` 补齐），索引写入侧按资产授权填 `acl_policy`；资产级授权模型本身 P2。
5. `retrieval_query_trace` 写入（P1）：每次检索持久化可复现诊断，接入评测回放与运营排查。
6. 阈值 profile 化（P2）：门控阈值/通道 limit 支持数据集级覆盖与 AB（strategy_version 真实化），替代 profiles.py 硬编码。
7. SCHEMA_FALLBACK（P2）：ASSISTED 兜底通道的大库表级召回（替代全量物理 schema 注入）。

### 5.4 规划（AnalysisPlanner，新建）

职责：把"理解 + 绑定资产 + verified 命中 + instructions"编译为 AnalysisPlan。

1. **规则直出通道（P1，覆盖大多数流量）**：单查询意图（L1-L2）由绑定结果确定性构造单节点计划——这是现状 STRICT 链路的等价物，零新增 LLM 成本；CROSS_MODEL 的 `multi_query_plans`（历史上检索层已产出但零消费）在此转为多 QueryTask 计划。当前实现已为每个子查询保存独立 `SemanticQueryPlan` 与验证报告，执行前逐个要求 `PROVEN`，并按 QueryTask 顺序选择计划；时间范围回投影会同步刷新所有子计划。真实数据集 243 已完成人工契约确认并切换 STRICT，模型/指标/维度子计划可在真实数据源编译和执行；模式选择对多指标理解结果补充 PLAN 路由，避免检索层已识别 CROSS_MODEL 却继续只编译首个子计划。端到端 CROSS_MODEL 的结果汇总仍要求问题理解明确产出比较/计算形态。

当前真实测试数据集仍不能直接切换 STRICT：数据集级 readiness 必须先满足模型粒度、指标契约、维度绑定和默认时间维度等完整契约门槛。对于未达标数据，系统只生成并记录非 `PROVEN` 的严格子计划，禁止绕过验证进入 SQL 执行。
2. **LLM 规划通道（P1）**：L3-L4 一次结构化输出调用产出计划草案；prompt 输入 = 理解 JSON + 绑定资产清单（含口径描述）+ 相似 verified 计划 + instructions + ComputeTask 操作集说明；输出经 JSON Schema 校验 + 逐节点语义校验（复用 PROVEN 链），失败带具体校验错误重试一次，仍失败降级：可拆则拆为已证明的子集 + 说明，不可拆则按四档路由处置。
3. **归因模板（P2）**：'为什么变化'类问题优先走确定性模板（总变化→逐维贡献分解：加法指标差值分解、比率指标简化贡献），产出标准计划；模板覆盖不了的进 RESEARCH。
4. **多轮 patch（P1）**：追问经重写判定为增量修改时，直接在上一 AnalysisPlan 上打补丁（换 time_range/维度/筛选），重走校验后执行——比全流程重跑快且稳定。
5. 计划持久化进 `derived_state`，作为澄清恢复/重试/审计/评测回放的载体。

### 5.5 校验与置信度路由

1. **节点级**：沿用语义计划校验（版本一致/指标维度身份/关系路径与聚合安全/时间语义/过滤）+ 编译产物比对 + SQL 三道闸。新增（P1）：EXPLAIN dry-run 可选开关（对标 WrenAI dry-plan）；ComputeTask 的 schema 校验（输入结果集必须包含 join_on/expr 引用列）。
2. **计划级（P1）**：DAG 无环、预算内（节点数 ≤ 配置上限）、presentation 引用存在、口径一致性（对比节点的指标口径必须同源）。
3. **置信度分档（P1）**：输入信号 = 绑定证据等级（exact/alias > rerank > lexical/dense）、校验状态、verified 命中、是否兜底通道、（ASSISTED 下可选）多候选一致率。四档：
   - **高**：直答；
   - **中**：直答 + 口径卡片置顶 + "建议核对"提示；
   - **歧义**：澄清（选项式，候选即绑定层过门槛资产）；
   - **低/越界**：拒答，附原因与建议问法/可问资产；ASSISTED 数据集可降级到兜底通道并标注。
4. **自检增强（P3，选做）**：对高价值查询生成更小的验证 SQL 主动核实过滤值/时间窗/join（对标 Genie Inspect）。

### 5.6 执行

1. **DAG 执行器（P1）**：无依赖 QueryTask 并行（复用 `apps/tool` 并行批次与预算基建），每 SQL 独立过三道闸与超时；节点失败不阻塞独立分支，进入修复子循环（预算沿用 SQL 修正 ≤2）。
2. **ResultStore（P1）**：见 4.2；全量行只落 Artifact 与前端，LLM 只见摘要（修复 D17/D11——摘要截断改为结构化截断：保留 schema+统计+前 N 行，绝不产出非法 JSON）。
3. **后台执行器（P2，修复 D6）**：Run 执行从 SSE 生成器剥离为后台任务（进程内 worker 起步，保留升级为队列的接口）；SSE 变为事件订阅（断线用现有 after_sequence 补拉恢复）；启动时对账 running 态 Run（超时判定 interrupted 可恢复/可重跑）。取消信号改 LISTEN/NOTIFY 或内存标记 + DB 兜底（替代 50ms 轮询）。
4. 预算体系（P1）：按模式配置（FAST/PLAN/RESEARCH 各自的步数/token/墙钟/查询数上限），替代单一全局 12 步。

### 5.7 结果计算（ComputeEngine，新建，P1）

- 实现：进程内 DuckDB——结果集注册为表，ComputeTask 编译为 DuckDB SQL 执行；产出新命名结果集。选 DuckDB 的理由：SQL 语义与主链路一致、跨结果集 join/窗口/透视原生支持、无额外服务依赖。
- 操作白名单（P1）：`compare`（多结果集按键对齐 + 差值/增幅）、`growth`（时序环比/同比率）、`share`（占比）、`topn_other`（TopN+其他归并）、`pivot`、`expr`（受限表达式：四则/聚合/条件，白名单函数，禁止子查询与外部引用）。
- 安全性质：只读内存数据、无外部 IO、表达式白名单校验——不引入新的注入面。
- P2/P3：归因分解算子（贡献度）、简单时序外推（预测 first-cut）、RESEARCH 模式允许受限脚本计算（沙箱评估后再定）。

### 5.8 回答生成（AnswerComposer，改造自 AgentFinalizationService）

1. **输入重构（P1）**：不再喂全量行——输入 = 各结果集的 schema/统计摘要/关键行（TopN、极值、总计）+ 计划与口径元数据 + 用户偏好（记忆）。
2. **引用绑定（P1）**：作答模型输出结构化 claim 列表，每个数字 claim 附结果集引用（result_set_id + 定位）；服务端校验数字确实存在于引用位置，不通过则该 claim 降级为表格直出 + 重试一次。达成"幻觉数字 = 0"目标的机制保证。
3. **口径卡片（P1）**：每次回答固定附：使用的指标（含定义）、维度、过滤（含值归一映射）、时间范围（含解析结果）、SQL 可展开、是否命中认证口径。对标"过程可验证"共识。
4. **图表升级（P1）**：chart spec 扩展为 table / bar(grouped|stacked) / line(multi-series) / area / pie / combo(bar+line) / pivot / KPI 卡；spec 由计划的 presentation hint + 结果集形状规则推导，LLM 只做微调选择；沿用 g2-ssr 渲染。
5. **分级作答（P1）**：FAST 单段结论+图；PLAN 结论+对比要点+图组；RESEARCH 结构化报告（发现/证据引用/建议/未验证假设）。软收口（预算耗尽）不再拼接字符串，走同一 Composer 的"部分结果"模板（修复 D7 兜底体验）。
6. 追问建议保留，来源改为：计划可延伸方向（下钻维度/换时段）+ 数据集 onboarding 问题。

### 5.9 记忆

机制保留（doc 29 设计合格），P1 激活：`CHATBI_MEMORY_*` 灰度开启（treatment 起 10%）；沉淀点对齐新架构——成功 Run 记 query_shape 偏好（已有）+ 计划模板偏好（新增：常用对比口径/图表偏好）；澄清选择继续沉淀。P2 接运营台：管理员可见记忆采用率报表（框架已有），标注集从灰度数据起建。守住现有安全边界：记忆永不绑定资产 ID/SQL。

### 5.10 权限

1. 三道闸保持不变（执行前全量复验的设计明确保留）。
2. P0：检索 ACL 请求侧接通（见 5.3.4）。
3. P2：语义资产级授权（角色→数据集内指标/维度可见性；含税/不含税类多口径按角色路由）；列权限增加脱敏选项（当前仅拒绝）；审计事件补齐问数执行记录（谁/何时/哪些表/SQL/行数）并恢复审计查询 API。
4. P2：限流与配额（用户/租户 QPS、并发 Run、每日问数与 token 配额——BudgetGuard 之上的租户层）。

### 5.11 观测

1. P0：`AGENT_TRACING_ENABLED` 默认开（采样 10% 起）、OTLP 出口配置化；事件契约新增 plan/task/compute domain（沿用 kind×phase 两轴）。
2. P1：metrics 层（QPS/成功率/各阶段延迟/token 与成本/澄清率/拒答率/兜底率），Prometheus 或 OTEL metrics 二选一；token 用量聚合到租户/用户/阶段维度（成本核算地基）。
3. P2：SSE 心跳与背压、事件保留期归档（doc 16 §10 欠账）；四套记录（event/trace/chat_log/audit）统一检索面。

### 5.12 评测与运营闭环（新建平台能力；2026-08-16 调整：平台建设下调至 P2 与运营闭环合并，P0–P1 以黄金题集 JSONL + 现有脚本人工跑批过渡）

1. **评测资产（题集 P0 起 / 平台表 P2）**：P0 起黄金题集以版本化 JSONL 维护（question、dataset、gold 期望、tags：能力等级/口径类型），种子 = fresh_20 + 20q 集 + verified queries 同源转化，每类新能力（值归一/同环比/派生指标/归因）落地时必须附评测题；P2 建 `eval_case` 等平台表并导入。
2. **判分（P0–P1 人工 / P2 自动）**：P0–P1 沿用人工核验（fresh_20 报告样式）+ 绑定/理解分层断言脚本；P2 平台化后主判 = 执行结果集对比（列名无关、排序容差、数值 4 位有效数字），回答质量 LLM-judge 仅作观测不作门禁。
3. **运行方式（P0–P1 人工跑批 / P2 平台化）**：P0–P1 阶段末与重大合并后人工跑批；P2 起 CI 冒烟集（~15 题，PR 门禁）+ 夜间全量 + 三连跑漂移检测，结果入库出趋势（哪次提交掉了哪些题）。
4. **运营闭环（P2 产品化）**：线上赞踩→badcase 队列（打标：理解错/绑定错/口径错/计算错/回答错）→处置动作直达资产编辑（补别名/补维值/建 verified query/改 instructions）→自动触发相关评测题回归验证→已解决归档。对标 Quick BI 问数运营 + Genie Monitor。
5. **晋升闭环（P2）**：成功 Run 且用户点赞 → 候选 verified query → 管理员审核 → verified（进检索与评测）；从认证计划泛化语义资产建议（如高频过滤值→维值别名建议）。

---

## 6. 保留 / 替换 / 删除清单

### 6.1 保留（增强）

| 模块 | 处置 | 增强点 |
|------|------|--------|
| `apps/semantic` 存储模型 + 契约三表 | **保留** | 派生指标/预聚合/快照编译（P1）、instructions 资产（P1）、Ossie 导出（P3） |
| `SemanticSQLCompiler` + PROVEN 计划链 | **保留** | 表达力补课（§5.1.2）、契约驱动 join 路径（P1） |
| `apps/retrieval` 平台 | **保留** | VALUE 槽、exemplar、ACL、trace 持久化、阈值 profile 化 |
| `apps/datasource` 查询安全服务（三道闸） | **保留** | 列脱敏选项（P2） |
| `apps/temporal` | **保留** | `time_ranges[]`/ComparisonSpec 支持（P1） |
| `apps/tool` 运行时（注册/预算/并发/可信参数） | **保留** | 降级为执行基础设施，不再承担流程驱动 |
| `apps/event` + `apps/trace` | **保留** | 新 domain、OTEL 默认开、metrics 层 |
| `apps/memory` | **保留** | 激活灰度、对齐新沉淀点 |
| `apps/conversation`、澄清挂起/恢复、取消状态机 | **保留** | 澄清载荷扩展到全部歧义类型；取消信号去轮询化（P2） |
| `understanding` 服务（重写+统一理解） | **保留** | 分诊 category、意图扩展、澄清全覆盖 |
| `access_control` 权限模型 | **保留** | 资产级授权、审计补齐（P2） |
| 未提交 WIP（统一理解重写、policy 收敛、维度继承） | **保留合入** | 作为 P0 的一部分；G23 张力按 §5.1.5 化解 |

### 6.2 替换

| 现状 | 替换为 | 理由 | 阶段 |
|------|--------|------|------|
| `tool_visibility.py` 状态机驱动的 ReAct 循环 | 三模式编排（FAST 规则直出 / PLAN 规划驱动 / RESEARCH 有界循环） | 修复 D1/D2/D4/D9：单查询锁死、死局、按钮化 ReAct 的成本错配 | P1 |
| 单槽 `last_execution` + `query-0` artifact | AnalysisPlan + ResultStore 命名结果集 | 多查询/跨查询计算的前提 | P1 |
| `FinishTool` + `AgentFinalizationService`（全量行两次 LLM） | AnswerComposer（摘要输入 + 引用绑定 + 口径卡片 + 图表升级） | 修复 D7/D17，幻觉数字清零 | P1 |
| SSE 生成器内联执行 | 后台执行器 + 事件订阅 + 对账恢复 | 修复 D6 | P2 |
| 直接回答闸门（chitchat 死代码） | 理解阶段 category 分诊 | 修复 D3 | P0 |
| Prompt 硬编码散落 | instructions 资产（数据集级）+ 提示词版本化 | 治理与灰度 | P1-P2 |
| `TimeRange` 单区间 | `time_ranges[]` + ComparisonSpec | 同环比表达 | P1 |
| observation 2000 字符腰斩截断 | 结构化截断（schema+统计+前 N 行） | 修复 D11 | P0 |
| 一次性评测脚本 | 评测平台（case 库 + CI 门禁 + 趋势）；P0–P1 过渡期为黄金题集 JSONL + 脚本人工跑批 | 缺陷六 | P2（2026-08-16 下调） |

### 6.3 删除（含退役条件）

| 对象 | 条件与时机 |
|------|-----------|
| Graph/Workflow 问数链路（`orchestration/graph/`、workflow_engine 对 chatbi 的绑定） | 本方案不投入；P2 当 Agent 链路黄金集 ≥85% 且能力覆盖 Graph 现有场景后正式下线，删除双执行器分叉（`execution_type`）与 Graph 专用理解双轨（INTENT/DIMENSION prompts、`_repair_dimension_coverage` 等） |
| 编译器 LEGACY 启发式（`_match_elements`、`_repair_element_by_candidate_tables`） | STRICT 成为默认后删除（P2，doc 27 既定要求） |
| `semanticEnforcement: LEGACY` 模式 | 数据集完成契约 backfill 后移除，只留 STRICT/ASSISTED |
| KNOWLEDGE_EVIDENCE / SCHEMA_FALLBACK 空转注册 | P0 先移除注册（诚实性），P2 按 §5.3 真实现后再注册 |
| `latest_successful_rewritten_question` 等死包装、`headless_asset_document` 相关残留兼容层 | P0-P1 随手清理 |
| 旧 embedding 遗留列（data_training.embedding、core_field 弃用列） | 独立清理窗口（低优先级） |
| `headless_*` 表名改名 | **不做**（维持 doc 12-14 决策：价值低风险高，仅文档层澄清命名历史） |

---

## 7. 分阶段落地

### P0 止血与地基（~2-4 周）——不动架构，先消灭"必然失败"并建立度量

| # | 事项 | 对应缺陷 |
|---|------|---------|
| 1 | 澄清全覆盖：所有 validation reason_code 可澄清，删除零工具死局 | D2 |
| 2 | 分诊 category：chitchat/meta/out_of_scope 正常处置 | D3 |
| 3 | tool_visibility 死角修补（STRICT 非 PROVEN → 澄清或拒答而非零工具；finish 失败 → 部分作答） | D4/D7 |
| 4 | VALUE 槽值归一（规划器生成 VALUE 槽 → canonical 替换/澄清） | 缺陷三 |
| 5 | exemplar/verified 命中进标准路径 + `verified_query` 表结构升级 | 缺陷三 |
| 6 | 指标级 filter_sql 进编译；observation 结构化截断 | 缺陷二/D11 |
| 7 | 检索 ACL 请求侧接通；空转 profile 移除注册 | 缺陷三 |
| 8 | 评测最低保障：黄金题集 JSONL + 现有脚本人工跑批（平台建设下调至 P2） | 缺陷六 |
| 9 | Trace/OTEL 默认开（采样）（badcase 队列随评测平台移至 P2） | 缺陷六 |
| 10 | 合入当前 WIP（统一理解、policy 收敛） | — |

**验收**：黄金集严格正确 ≥65%；死局类失败 = 0；阶段末脚本人工跑批出报告。

### P1 复杂查询能力（~4-8 周）——本方案核心增量

| # | 事项 |
|---|------|
| 1 | AnalysisPlan / QueryTask / ComputeTask / ResultStore 数据模型与持久化 |
| 2 | FAST 规则直出通道（现有 STRICT 链路平移，LLM 调用降至 ≤4） |
| 3 | PLAN 规划通道：规划 LLM 结构化输出 + 逐节点 PROVEN 校验 + 失败重试/降级 |
| 4 | ComputeEngine（DuckDB）+ 操作白名单；`multi_query_plans` 消费 |
| 5 | 意图扩展（comparison/composition/multi_step、time_ranges[]、ComparisonSpec） |
| 6 | 指标表达力：derived/ratio/时间偏移/预聚合/快照/HAVING 编译 |
| 7 | AnswerComposer：引用绑定 + 口径卡片 + 图表 spec 升级 + 部分作答 |
| 8 | 置信度四档路由 + ASSISTED 兜底通道（标注非认证口径） |
| 9 | 多轮计划 patch；记忆灰度开启；metrics 观测层；`retrieval_query_trace` 写入 | **首版已完成（2026-08-16）** |
| 10 | instructions 资产 + 消费；STRICT 逐数据集推广启动 | **首版已完成（2026-08-16）**：迁移 114、CRUD/API、Schema 投影、理解/规划提示词固定槽位和 STRICT readiness 报告已落地；推广仍按数据集人工执行。 |

**实施状态（2026-08-16）**：P1-7 首版已落地。AnswerComposer 将模型输出限制为结构化回答与 claims，服务端校验数字 claim 的结果集、字段和行定位；失败最多重试一次，仍失败统一表格直出。口径卡片和图表 spec 由服务端规则生成，并通过 answer/run-finished 事件输出。FAST/PLAN 已接入，默认 `react_legacy` 仍保留 `AgentFinalizationService` 过渡链路。

**实施状态（2026-08-16）**：P1-8 首版已落地。置信度服务按绑定证据、校验状态、verified 命中和兜底通道输出 direct/disclose/clarify/reject，并将依据写入运行态；ASSISTED 兜底只在数据集策略与全局开关同时开启时生效，生成 SQL 后仍走统一 DatasourceQueryService 校验和执行，且显式标记非认证口径。

**验收**：新增对比/复合口径/多查询评测集 ≥80%；FAST 路径 LLM ≤4 次；回答数字溯源 100%；黄金集 ≥80%。

### P2 企业化运营（~8-12 周）

RESEARCH 模式（含归因模板）first-cut；后台执行器 + 断线恢复 + 取消去轮询；评测平台（自 P0 下调：eval 三表/结果集判分/漂移/CI 门禁/badcase 队列）、verified 晋升闭环与运营台产品化；资产级授权 + 列脱敏 + 审计补齐 + 限流配额 + 成本报表；KNOWLEDGE_EVIDENCE 落地；阈值 profile 化；Graph 链路按条件下线；LEGACY 清理。
**验收**：黄金集 ≥85% 且发布判定通过；运营看板上线（badcase 处理时长、澄清率、拒答率、成本）；断连/重启无悬挂 Run。

### P3 增强（持续）

深度研究报告完善（多假设/引用完整性）；简单时序外推预测（评估后）；跨数据集路由（多语义模型自动选择，对标 Cortex `semantic_models[]`）；AI 辅助建模冷启动（从 schema/查询日志起草资产→专家确认）；Ossie 导出；对外 MCP 能力增强；生成后验证 SQL 自检（Genie Inspect 式）。

### 依赖与排序说明

P0 全部事项相互独立可并行；P1 中 1→2/3→4/7 有依赖链，5/6 可并行先行；评测在 P0–P1 以黄金题集 + 脚本人工跑批作为验收工具，平台化在 P2（2026-08-16 下调决策）。人力估算按 2-3 人后端投入；若人力紧张，P1 的 6（指标表达力）可拆出与 4 并行交付。

---

## 8. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| 规划 LLM 产出计划质量不稳 | 结构化输出 + 逐节点确定性校验兜底 + 失败降级路径；规划题独立评测集；大多数流量走规则直出不经规划 LLM |
| ComputeEngine 引入 DuckDB 新依赖 | 进程内嵌入无服务依赖；操作白名单限制面；P1 仅五类算子渐进放开 |
| 三模式并存期间行为分叉 | FAST 与现链路语义等价先行灰度；`execution_mode` 落 Run 供评测分组对比 |
| STRICT 推广受契约 backfill 进度制约 | ASSISTED 过渡态兜底；backfill 工具已有，按数据集排期 |
| 评测集规模小、判分误判 | 判分容差规则先在 fresh_20 上标定；verified 同源持续扩集；人工抽检通道保留 |
| 后台执行器改造触及 SSE 契约 | 事件契约不变（订阅+补拉已有），仅执行宿主迁移；放 P2 且先进程内 worker |
| 团队带宽 | P0 均为小改动可并行；P1 核心三件（Plan/ResultStore/Compute)集中攻坚 |

**开放问题（需要产品/团队决策）**
1. RESEARCH 模式的默认开放范围（全员 or 数据集白名单）与预算上限定价；
2. ASSISTED 兜底在哪些数据集开放（可信优先 or 覆盖优先的租户级默认）；
3. Graph 下线的具体时间点与存量会话迁移策略；
4. 多数据源需求的真实优先级（影响 P3 排序）；
5. 各阶段模型选型与路由（理解/规划/作答是否分档用不同模型）——建议 P1 时以评测数据定。

---

## 附录 A. 调研来源速查

- **国际产品**：Databricks Genie（docs.databricks.com/aws/en/genie-agents/）、Snowflake Cortex Analyst 与 semantic-view-yaml-spec（docs.snowflake.com）、ThoughtSpot Spotter（docs.thoughtspot.com）、Power BI Copilot（learn.microsoft.com）、Looker Conversational Analytics（docs.cloud.google.com）、Tableau Pulse（help.tableau.com）、Amazon Quick（docs.aws.amazon.com/quick/）、Zenlytic（docs.zenlytic.com）、Honeydew（honeydew.ai/docs/）
- **国内产品**：Quick BI 智能问数（help.aliyun.com/zh/quick-bi/）、FineChatBI（help.fanruan.com/finebi7.0/）、网易数帆有数 ChatBI、观远、火山引擎 DataWind、腾讯云 BI ChatBI、Sugar BI（cloud.baidu.com/doc/SUGAR/）、Kyligence、数势 SwiftAgent、衡石 SENSE、Aloudata（NL2MQL2SQL）
- **开源**：SuperSonic（github.com/tencentmusic/supersonic，S2SQL 中间表示 + Mapper/Parser/Corrector/Translator 流水线）、WrenAI（MDL + dry-plan + correctness as primitives）、Vanna（RAG 三件套与 2.0 Agent 化教训）、DB-GPT（AWEL）、Dataherald（golden SQL / instructions / query-history 冷启动）、dataease/SQLBot（上游基线）
- **学术**：BIRD（bird-bench.github.io）、Spider 2.0（spider2-sql.github.io）、BEAVER、BIRD-Interact；XiYan-SQL（M-Schema）、CHASE-SQL、CHESS、OpenSearch-SQL、Alpha-SQL、ReFoRCE、Arctic-Text2SQL-R1、OmniSQL；schema linking 争议（arXiv:2408.07702）、AMBROSIA/CLAMBER（歧义）、TrustSQL（拒答）、DAIL-SQL（few-shot）
- **语义层**：dbt MetricFlow（docs.getdbt.com/docs/build/）、Cube（cube.dev/docs 与配对基准 arXiv:2604.25149）、LookML（cloud.google.com/looker/docs/）、Snowflake semantic views、Databricks metric views + UC semantics、AtScale SML（github.com/semanticdatalayer/SML）、Malloy、Apache Ossie（ossie.apache.org）

## 附录 B. 术语对照

| 本方案术语 | 含义 | 对标 |
|-----------|------|------|
| AnalysisPlan | 一次问数的执行合同（QueryTask/ComputeTask DAG） | — |
| QueryTask | 语义查询规格，编译为单条 SQL | SemanticQueryPlan 超集 |
| ComputeTask | 跨结果集确定性计算 | — |
| ResultStore | 命名结果集仓库（Run Artifact） | — |
| FAST / PLAN / RESEARCH | 三种执行模式 | Genie Chat/Agent mode、Spotter Search/Research |
| verified query | 人工认证的问题→语义计划/SQL 资产 | Snowflake VQR、Dataherald golden SQL |
| instructions 资产 | 数据集级模块化指令（生成规则/分类护栏） | Snowflake module_custom_instructions |
| ASSISTED | 语义优先 + 受控 NL2SQL 兜底的数据集模式 | Snowflake Routing Mode、腾讯云专业模式 |
| 四档路由 | 直答/披露/澄清/拒答 | TrustSQL、ReFoRCE 推迟机制 |
